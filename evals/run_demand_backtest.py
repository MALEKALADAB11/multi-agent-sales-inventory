"""
Banc de prévision de la demande par référence — comparaison de modèles.

Protocole
---------
Rolling-origin à origine expansive sur les couples (SKU, boutique) franchissant
le seuil d'inclusion de 90 jours d'historique retenu dans le rapport.
Pour chaque pli : entraînement sur tout le passé disponible, prévision sur les
`fold_days` jours suivants, comparaison aux ventes réelles.

Modèles comparés
----------------
  seasonal_naive          référence obligatoire (valeur observée à J-7)
  mean_7 / mean_28        moyennes mobiles
  holt_winters_seasonal7  lissage exponentiel saisonnier (implémentation interne
                          du moteur de production, période 7)
  statsforecast_ensemble  AutoETS + AutoARIMA + AutoCES moyennés (si installé)
  mstl                    décomposition saisonnière multiple (si série >= 730 j)

Métriques : WAPE, MAE, RMSE, MASE, biais — définitions du chapitre 2 du rapport.

Usage :
    python -m evals.run_demand_backtest --couples 60 --folds 3 --fold-days 14
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

OUT = Path(__file__).resolve().parent / "results" / "demand_backtest.json"
MIN_HISTORY = 90          # seuil d'inclusion du rapport
SEASON = 7


# ── données ──────────────────────────────────────────────────────────────────
def _conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "ooredoo_sales"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "admin"),
    )


def load_series(n_couples: int) -> list[dict]:
    """Séries journalières continues (jours sans vente = 0) des couples les plus denses."""
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT store_id, sku,
                   COUNT(DISTINCT record_date) AS days,
                   MIN(record_date) AS d0, MAX(record_date) AS d1,
                   SUM(quantity_sold) AS total
            FROM inventory.sales_history
            GROUP BY store_id, sku
            HAVING COUNT(DISTINCT record_date) >= %s
            ORDER BY SUM(quantity_sold) DESC
            LIMIT %s
        """, (MIN_HISTORY, n_couples))
        couples = cur.fetchall()

        series = []
        for c in couples:
            cur.execute("""
                SELECT d::date AS d, COALESCE(SUM(h.quantity_sold), 0)::float AS q
                FROM generate_series(%(d0)s::date, %(d1)s::date, '1 day') AS d
                LEFT JOIN inventory.sales_history h
                       ON h.record_date = d::date
                      AND h.store_id = %(store)s AND h.sku = %(sku)s
                GROUP BY d ORDER BY d
            """, {"d0": c["d0"], "d1": c["d1"], "store": c["store_id"], "sku": c["sku"]})
            vals = [float(r["q"]) for r in cur.fetchall()]
            series.append({"store_id": c["store_id"], "sku": int(c["sku"]), "y": vals})
    return series


# ── modèles ──────────────────────────────────────────────────────────────────
def seasonal_naive(train: list[float], h: int) -> np.ndarray:
    if len(train) < SEASON:
        return np.full(h, float(np.mean(train)) if train else 0.0)
    return np.array([train[-SEASON + (i % SEASON)] for i in range(h)])


def moving_mean(train: list[float], h: int, w: int) -> np.ndarray:
    return np.full(h, float(np.mean(train[-w:])) if train else 0.0)


def holt_winters(train: list[float], h: int,
                 alpha: float = 0.3, beta: float = 0.1, gamma: float = 0.3) -> np.ndarray:
    """Lissage exponentiel saisonnier additif, période 7 — implémentation interne."""
    y = list(train)
    if len(y) < 2 * SEASON:
        return moving_mean(y, h, SEASON)
    level = float(np.mean(y[:SEASON]))
    trend = (float(np.mean(y[SEASON:2 * SEASON])) - level) / SEASON
    seas = [y[i] - level for i in range(SEASON)]
    for i, v in enumerate(y):
        s_idx = i % SEASON
        last_level = level
        level = alpha * (v - seas[s_idx]) + (1 - alpha) * (level + trend)
        trend = beta * (level - last_level) + (1 - beta) * trend
        seas[s_idx] = gamma * (v - level) + (1 - gamma) * seas[s_idx]
    return np.array([max(0.0, level + (i + 1) * trend + seas[(len(y) + i) % SEASON])
                     for i in range(h)])


def statsforecast_models(train: list[float], h: int, use_mstl: bool):
    """Ensemble AutoETS + AutoARIMA + AutoCES, ou MSTL si l'historique le permet."""
    import pandas as pd
    from statsforecast import StatsForecast
    from statsforecast.models import AutoARIMA, AutoCES, AutoETS, MSTL

    df = pd.DataFrame({
        "unique_id": "s",
        "ds": pd.date_range("2024-01-01", periods=len(train), freq="D"),
        "y": train,
    })
    models = [MSTL(season_length=[7, 365])] if use_mstl else [
        AutoETS(season_length=SEASON, damped=True),
        AutoARIMA(season_length=SEASON),
        AutoCES(season_length=SEASON),
    ]
    sf = StatsForecast(models=models, freq="D", n_jobs=1)
    pred = sf.forecast(df=df, h=h)
    cols = [c for c in pred.columns if c not in ("unique_id", "ds")]
    vals = pred[cols].mean(axis=1).to_numpy()
    return np.clip(np.nan_to_num(vals, nan=float(np.mean(train[-SEASON:]))), 0, None)


# ── métriques (definitions du chapitre 2) ────────────────────────────────────
def metrics(actual: np.ndarray, pred: np.ndarray, naive: np.ndarray) -> dict:
    err = np.abs(actual - pred)
    denom = np.sum(np.abs(actual))
    mae = float(np.mean(err))
    mae_naive = float(np.mean(np.abs(actual - naive))) or 1e-9
    return {
        "wape_pct": round(100 * float(np.sum(err)) / denom, 2) if denom else None,
        "mae": round(mae, 3),
        "rmse": round(float(np.sqrt(np.mean((actual - pred) ** 2))), 3),
        "mase": round(mae / mae_naive, 3),
        "bias_pct": round(100 * float(np.sum(pred - actual)) / denom, 2) if denom else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--couples", type=int, default=60)
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--fold-days", type=int, default=14)
    ap.add_argument("--no-statsforecast", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    series = load_series(args.couples)
    print(f"{len(series)} couples (SKU, boutique) charges "
          f"(>= {MIN_HISTORY} jours d'historique)")

    use_sf = not args.no_statsforecast
    if use_sf:
        try:
            import statsforecast  # noqa: F401
        except Exception as e:
            print(f"statsforecast indisponible ({str(e)[:60]}) — modele ignore")
            use_sf = False

    acc: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    n_eval, n_mstl = 0, 0

    for si, s in enumerate(series, 1):
        y = s["y"]
        need = args.folds * args.fold_days + MIN_HISTORY
        if len(y) < need:
            continue
        for f in range(args.folds):
            end = len(y) - (args.folds - f - 1) * args.fold_days
            start_test = end - args.fold_days
            train, actual = y[:start_test], np.array(y[start_test:end])
            if len(train) < MIN_HISTORY or actual.sum() == 0:
                continue
            h = len(actual)
            naive = seasonal_naive(train, h)
            preds = {
                "seasonal_naive": naive,
                "mean_7": moving_mean(train, h, 7),
                "mean_28": moving_mean(train, h, 28),
                "holt_winters_seasonal7": holt_winters(train, h),
            }
            if use_sf:
                mstl_ok = len(train) >= 730
                try:
                    key = "mstl" if mstl_ok else "statsforecast_ensemble"
                    preds[key] = statsforecast_models(train, h, mstl_ok)
                    n_mstl += int(mstl_ok)
                except Exception as e:
                    if si == 1 and f == 0:
                        print(f"  statsforecast en echec: {str(e)[:80]}")
            for name, p in preds.items():
                acc[name]["abs_err"].append(float(np.sum(np.abs(actual - p))))
                acc[name]["vol"].append(float(np.sum(np.abs(actual))))
                acc[name]["sq_err"].append(float(np.sum((actual - p) ** 2)))
                acc[name]["signed"].append(float(np.sum(p - actual)))
                acc[name]["n"].append(h)
            n_eval += 1
        if si % 20 == 0:
            print(f"  ... {si}/{len(series)} couples ({time.time()-t0:.0f}s)")

    naive_mae = sum(acc["seasonal_naive"]["abs_err"]) / max(1, sum(acc["seasonal_naive"]["n"]))
    results = {}
    for name, d in acc.items():
        n = sum(d["n"])
        vol = sum(d["vol"])
        mae = sum(d["abs_err"]) / n
        results[name] = {
            "wape_pct": round(100 * sum(d["abs_err"]) / vol, 2),
            "mae": round(mae, 3),
            "rmse": round(math.sqrt(sum(d["sq_err"]) / n), 3),
            "mase": round(mae / naive_mae, 3),
            "bias_pct": round(100 * sum(d["signed"]) / vol, 2),
            "n_points": n,
        }

    ranked = sorted(results.items(), key=lambda kv: kv[1]["wape_pct"])
    out = {
        "suite": "demand_backtest",
        "protocol": {
            "couples": len(series), "folds": args.folds, "fold_days": args.fold_days,
            "min_history_days": MIN_HISTORY, "evaluations": n_eval,
            "mstl_folds": n_mstl,
        },
        "results": results,
        "ranking_by_wape": [{"model": k, "wape_pct": v["wape_pct"]} for k, v in ranked],
        "winner": ranked[0][0] if ranked else None,
        "elapsed_s": round(time.time() - t0, 1),
        "run_at": datetime.now().isoformat(timespec="seconds"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'Modele':28s} {'WAPE %':>8s} {'MAE':>8s} {'RMSE':>8s} {'MASE':>7s} {'Biais %':>8s}")
    for k, v in ranked:
        print(f"{k:28s} {v['wape_pct']:8.2f} {v['mae']:8.3f} {v['rmse']:8.3f} "
              f"{v['mase']:7.3f} {v['bias_pct']:8.2f}")
    print(f"\n{n_eval} evaluations · {out['elapsed_s']}s · ecrit -> {OUT}")


if __name__ == "__main__":
    main()
