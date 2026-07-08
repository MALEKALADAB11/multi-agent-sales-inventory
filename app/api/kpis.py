"""
kpis.py — KPIs d'évaluation du système agentique.
==================================================
Répond au besoin "évaluation quantitative" du PFE :

GET /api/v1/kpis
    Tableau de bord : adoption des recommandations (HITL, PO, incitations),
    santé du stock (ruptures, niveaux critiques), ventes vs objectif.

GET /api/v1/kpis/forecast-benchmark
    Backtest comparatif des moteurs de prévision (TimesFM vs StatsForecast
    AutoETS/Theta vs Chronos vs baseline numpy) sur l'historique réel :
    holdout des N derniers jours, WAPE + sMAPE + biais par moteur, avec
    recommandation du meilleur moteur. C'est la réponse empirique à
    "TimesFM ou Prophet ou autre ?" — mesurée sur NOS données.

Endpoints sync (def) → threadpool FastAPI ; psycopg2 une connexion par appel.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/kpis", tags=["kpis"])


def _conn():
    from app.core.config import config
    return psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_USER, password=config.DB_PASSWORD,
    )


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/kpis — tableau de bord d'évaluation
# ═══════════════════════════════════════════════════════════════════════════

@router.get("")
def get_kpis(store_id: Optional[str] = None, days: int = 30):
    """KPIs métier agrégés sur `days` jours (magasin ou global)."""
    from app.core.feedback_service import get_feedback_stats

    out: Dict[str, Any] = {
        "window_days": days,
        "store_id": store_id or "ALL",
        "adoption": get_feedback_stats(store_id=store_id, days=days),
        "stock": {},
        "sales": {},
    }

    conn = None
    try:
        conn = _conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sc = "AND store_id = %(sid)s" if store_id else ""
            params = {"sid": store_id, "days": days}

            # ── Santé du stock (photo actuelle) ─────────────────────────────
            cur.execute(f"""
                SELECT
                    COUNT(*)                                                    AS total_skus,
                    COUNT(*) FILTER (WHERE COALESCE(quantity_available, quantity, 0) <= 0)  AS ruptures,
                    COUNT(*) FILTER (WHERE COALESCE(quantity_available, quantity, 0) BETWEEN 1 AND 3)  AS critiques,
                    COUNT(*) FILTER (WHERE COALESCE(quantity_available, quantity, 0) BETWEEN 4 AND 10) AS bas
                FROM inventory.stock_levels
                WHERE TRUE {sc}
            """, params)
            row = cur.fetchone() or {}
            total = int(row.get("total_skus") or 0)
            ruptures = int(row.get("ruptures") or 0)
            out["stock"] = {
                "total_skus":   total,
                "ruptures":     ruptures,
                "critiques":    int(row.get("critiques") or 0),
                "bas":          int(row.get("bas") or 0),
                "taux_rupture_pct": round(ruptures / total * 100, 1) if total else None,
            }

            # ── Ventes vs objectif ──────────────────────────────────────────
            cur.execute(f"""
                SELECT date_only AS d, SUM(lig_ttc) AS ca
                FROM sales.transactions
                WHERE date_only >= CURRENT_DATE - (%(days)s)::int
                  AND date_only <= CURRENT_DATE
                  AND lig_ttc > 0 {sc}
                GROUP BY date_only ORDER BY date_only
            """, params)
            daily = [{"date": str(r["d"]), "ca": round(float(r["ca"]), 2)}
                     for r in cur.fetchall()]

            cur.execute(f"""
                SELECT SUM(objectif_ca) AS total_target
                FROM sales.objectifs
                WHERE agent_id IS NULL
                  AND date_objectif >= CURRENT_DATE - (%(days)s)::int
                  AND date_objectif <= CURRENT_DATE
                  {sc}
            """, params)
            trow = cur.fetchone() or {}
            total_target = float(trow.get("total_target") or 0)
            total_ca = sum(d["ca"] for d in daily)
            out["sales"] = {
                "total_ca":          round(total_ca, 2),
                "total_target":      round(total_target, 2) if total_target else None,
                "attainment_pct":    round(total_ca / total_target * 100, 1)
                                     if total_target else None,
                "daily_avg_ca":      round(total_ca / len(daily), 2) if daily else 0,
                "days_with_sales":   len(daily),
                "daily":             daily[-14:],   # 2 dernières semaines pour le chart
            }
    except Exception as e:
        logger.error("[KPIs] get_kpis: %s", e)
        out["error"] = str(e)[:200]
    finally:
        if conn:
            conn.close()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark forecast — TimesFM vs StatsForecast vs Chronos vs numpy
# ═══════════════════════════════════════════════════════════════════════════

def _metrics(actual: np.ndarray, pred: np.ndarray) -> Dict[str, float]:
    """WAPE + sMAPE + biais — robustes aux jours à zéro vente (retail)."""
    actual = np.asarray(actual, dtype=float)
    pred   = np.asarray(pred, dtype=float)[: len(actual)]
    denom  = np.abs(actual).sum()
    wape   = float(np.abs(actual - pred).sum() / denom * 100) if denom else None
    sm_den = (np.abs(actual) + np.abs(pred))
    smape  = float(np.mean(np.where(sm_den == 0, 0, 2 * np.abs(actual - pred) / np.where(sm_den == 0, 1, sm_den))) * 100)
    bias   = float((pred.sum() - actual.sum()) / denom * 100) if denom else None
    return {
        "wape_pct":  round(wape, 1) if wape is not None else None,
        "smape_pct": round(smape, 1),
        "bias_pct":  round(bias, 1) if bias is not None else None,
    }


def _try_prophet_forecast(train: List[float], horizon: int) -> Optional[List[float]]:
    """Prophet (si installé) — saisonnalité hebdo + annuelle, sans régresseurs."""
    try:
        import pandas as pd
        from prophet import Prophet
        df = pd.DataFrame({
            "ds": pd.date_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=horizon + 1),
                                periods=len(train), freq="D"),
            "y": train,
        })
        m = Prophet(weekly_seasonality=True,
                    yearly_seasonality=len(train) >= 365,
                    daily_seasonality=False)
        # Prophet est très verbeux — couper cmdstanpy
        import logging as _lg
        _lg.getLogger("cmdstanpy").setLevel(_lg.ERROR)
        m.fit(df)
        future = m.make_future_dataframe(periods=horizon, freq="D")
        fc = m.predict(future).tail(horizon)["yhat"].to_numpy()
        return [max(0.0, float(v)) for v in fc]
    except Exception as e:
        logger.warning("[KPIs] Prophet indisponible pour le benchmark: %s", e)
        return None


_timesfm_singleton = None
_timesfm_failed = False


def _try_timesfm_forecast(train: List[float], horizon: int) -> Optional[List[float]]:
    """
    TimesFM 2.5 zero-shot (API du package timesfm>=2.0) — lazy singleton.
    `import torch` doit précéder `import timesfm` : sur Windows, charger torch
    après certaines libs de l'app fait échouer c10.dll (WinError 1114).
    """
    global _timesfm_singleton, _timesfm_failed
    if _timesfm_failed:
        return None
    try:
        import torch  # noqa: F401 — ordre d'import DLL critique, voir docstring
        import timesfm
        if _timesfm_singleton is None:
            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
                "google/timesfm-2.5-200m-pytorch"
            )
            model.compile(timesfm.ForecastConfig(
                max_context=1024,
                max_horizon=max(64, horizon),
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                fix_quantile_crossing=True,
            ))
            _timesfm_singleton = model
        point, _quantiles = _timesfm_singleton.forecast(
            horizon=horizon,
            inputs=[np.asarray(train[-1024:], dtype=np.float32)],
        )
        return [max(0.0, float(v)) for v in np.asarray(point)[0][:horizon]]
    except Exception as e:
        logger.warning("[KPIs] TimesFM indisponible pour le benchmark: %s", e)
        _timesfm_failed = True
        return None


@router.get("/forecast-benchmark")
def forecast_benchmark(
    store_id: Optional[str] = None,
    sku: Optional[int] = None,
    holdout_days: int = 14,
    history_days: int = 365,
    include_timesfm: bool = True,
):
    """
    Backtest : entraîne sur [aujourd'hui - history_days, aujourd'hui - holdout_days],
    prédit les holdout_days derniers, compare aux ventes réelles.
    Sans `sku`, prend le meilleur vendeur de la période (série la plus dense).
    """
    if holdout_days < 3 or holdout_days > 60:
        raise HTTPException(status_code=400, detail="holdout_days doit être entre 3 et 60")

    conn = None
    try:
        conn = _conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sc = "AND store_id = %(sid)s" if store_id else ""
            params: Dict[str, Any] = {
                "sid": store_id, "hist": history_days, "sku": sku,
            }

            if sku is None:
                cur.execute(f"""
                    SELECT sku, SUM(quantity) AS q
                    FROM sales.transactions
                    WHERE date_only >= CURRENT_DATE - (%(hist)s)::int
                      AND lig_ttc > 0 {sc}
                    GROUP BY sku ORDER BY q DESC LIMIT 1
                """, params)
                r = cur.fetchone()
                if not r:
                    raise HTTPException(status_code=404, detail="Aucune vente sur la période")
                sku = int(r["sku"])
                params["sku"] = sku

            # Série journalière continue (jours sans vente = 0)
            cur.execute(f"""
                SELECT d::date AS d, COALESCE(SUM(t.quantity), 0) AS qty
                FROM generate_series(
                        CURRENT_DATE - (%(hist)s)::int,
                        CURRENT_DATE - 1, '1 day') AS d
                LEFT JOIN sales.transactions t
                       ON t.date_only = d::date AND t.sku = %(sku)s
                      AND t.lig_ttc > 0 {sc.replace('store_id', 't.store_id')}
                GROUP BY d ORDER BY d
            """, params)
            rows = cur.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[KPIs] forecast_benchmark DB: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])
    finally:
        if conn:
            conn.close()

    series = [float(r["qty"]) for r in rows]
    if len(series) < holdout_days + 30:
        raise HTTPException(status_code=422,
                            detail=f"Historique insuffisant ({len(series)} jours) pour un holdout de {holdout_days}")

    train  = series[:-holdout_days]
    actual = np.array(series[-holdout_days:], dtype=float)

    from app.inventory.forecasting.timeseries_engine import forecast as ts_forecast

    results: Dict[str, Any] = {}
    for engine in ("statsforecast", "chronos", "numpy"):
        try:
            fc = ts_forecast(train, horizon=holdout_days, force_engine=engine)
            engine_used = fc.get("engine", engine)
            pred = np.array(fc.get("forecast_values", []), dtype=float)
            if len(pred) < holdout_days:
                raise ValueError(f"forecast incomplet ({len(pred)} pts)")
            results[engine] = {"engine_used": engine_used, **_metrics(actual, pred)}
        except Exception as e:
            results[engine] = {"error": str(e)[:120]}

    pred_prophet = _try_prophet_forecast(train, holdout_days)
    if pred_prophet is not None:
        results["prophet"] = {"engine_used": "prophet",
                              **_metrics(actual, np.array(pred_prophet))}
    else:
        results["prophet"] = {"error": "Prophet non disponible dans cet environnement"}

    if include_timesfm:
        pred_tfm = _try_timesfm_forecast(train, holdout_days)
        if pred_tfm is not None:
            results["timesfm"] = {"engine_used": "timesfm-2.5-200m",
                                  **_metrics(actual, np.array(pred_tfm))}
        else:
            results["timesfm"] = {"error": "TimesFM non disponible dans cet environnement"}

    # Dédupliquer par moteur réellement utilisé (si une lib n'est pas installée,
    # plusieurs demandes retombent sur le même fallback numpy)
    seen_impl: Dict[str, str] = {}
    for name, r in results.items():
        impl = r.get("engine_used")
        if impl and impl not in seen_impl:
            seen_impl[impl] = name
    ranked = sorted(
        [(name, results[name]["wape_pct"]) for name in seen_impl.values()
         if results[name].get("wape_pct") is not None],
        key=lambda x: x[1],
    )
    best = ranked[0][0] if ranked else None

    return {
        "sku": sku,
        "store_id": store_id or "ALL",
        "history_days": len(series),
        "holdout_days": holdout_days,
        "train_points": len(train),
        "actual_total_units": float(actual.sum()),
        "engines": results,
        "ranking_by_wape": [{"engine": n, "wape_pct": w} for n, w in ranked],
        "best_engine": best,
        "note": (
            "WAPE = somme des erreurs absolues / volume réel (robuste aux jours à 0 vente). "
            "Moteurs comparés sur données réelles : TimesFM (zero-shot), Prophet, "
            "StatsForecast/Chronos si installés, baseline numpy (Holt)."
        ),
    }
