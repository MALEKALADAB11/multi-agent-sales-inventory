"""
ts_engine.py — Moteur de séries temporelles de l'Agent Analyste Sales.
=======================================================================
VRAI modèle de prévision — 100% déterministe, zéro LLM dans la boucle.

Pipeline statistique :
  1. Série journalière (CA/jour, 120j)  → Holt-Winters triple lissage
     exponentiel additif, saisonnalité hebdomadaire (period=7),
     grid-search (alpha, beta, gamma), backtest rolling-origin 28j → MAPE réel.
  2. Profil intraday par jour de semaine (8 semaines d'historique horaire) :
     part moyenne + écart-type du CA par heure → distribution cumulative.
  3. Prévision EOD hybride :
        eod_model   = prévision Holt-Winters du jour (avant ouverture)
        eod_unfold  = CA_actuel / part_cumulée(heure, DOW)  (déroulé intraday)
     pondération dynamique : confiance dans le déroulé ∝ part de journée écoulée.
  4. Détection de gap HORAIRE : pour chaque heure écoulée,
        attendu_h = eod_baseline × part(h, DOW)   vs   réel_h
     → déviation %, z-score vs distribution historique de l'heure,
       statut OK / WATCH / ALERT, tendance ACCELERATING/DECELERATING/STABLE.
  5. Prévision des prochaines heures (h+1..h+3) + demain (J+1).
  6. Urgence composite déterministe + faisabilité du rattrapage.

Moteurs : statsforecast AutoETS si installé, sinon Holt-Winters numpy interne
(benchmark Sprint 11 : Holt MAPE 4.4% ≥ Prophet sur ces données).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np

from app.sales.data.postgres_provider import (
    PATTERN_HORAIRE,
    get_pg_conn,
    normalize_store_id,
    _get_business_date,
    _get_daily_target,
)

logger = logging.getLogger(__name__)

STORE_OPEN_HOUR  = int(os.getenv("STORE_OPEN_HOUR",  "8"))
STORE_CLOSE_HOUR = int(os.getenv("STORE_CLOSE_HOUR", "20"))

# Seuils de détection de gap horaire (z-score et déviation relative)
HOURLY_Z_ALERT   = 2.0
HOURLY_Z_WATCH   = 1.0
HOURLY_DEV_ALERT = -0.35   # heure réalisée < 65% de l'attendu
HOURLY_DEV_WATCH = -0.20

try:  # moteur optionnel — requirements.txt le déclare mais il peut manquer
    from statsforecast import StatsForecast              # noqa: F401
    from statsforecast.models import AutoETS             # noqa: F401
    _SF_AVAILABLE = True
except Exception:
    _SF_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# 1. Holt-Winters additif saisonnier (numpy pur) + backtest
# ══════════════════════════════════════════════════════════════════════════════

def _hw_fit_forecast(y: np.ndarray, period: int, horizon: int,
                     alpha: float, beta: float, gamma: float) -> tuple[np.ndarray, np.ndarray]:
    """Triple lissage exponentiel additif. Retourne (fitted one-step, forecast horizon)."""
    n = len(y)
    seasons = n // period
    # Initialisation classique : niveau/tendance sur les 2 premiers cycles
    level = float(np.mean(y[:period]))
    trend = (float(np.mean(y[period:2 * period])) - level) / period if seasons >= 2 else 0.0
    seasonal = np.array([float(y[i]) - level for i in range(period)])

    fitted = np.zeros(n)
    for t in range(n):
        s_idx = t % period
        yhat = level + trend + seasonal[s_idx]
        fitted[t] = yhat
        err = float(y[t]) - yhat
        prev_level = level
        level = alpha * (float(y[t]) - seasonal[s_idx]) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasonal[s_idx] = gamma * (float(y[t]) - level) + (1 - gamma) * seasonal[s_idx]

    fc = np.array([
        level + (h + 1) * trend + seasonal[(n + h) % period]
        for h in range(horizon)
    ])
    return fitted, np.maximum(0.0, fc)


def _hw_gridsearch(y: np.ndarray, period: int = 7) -> dict:
    """Petit grid-search (27 combinaisons) minimisant le MAE one-step in-sample."""
    best = {"alpha": 0.3, "beta": 0.05, "gamma": 0.2, "mae": float("inf")}
    grid_a = (0.15, 0.3, 0.5)
    grid_b = (0.01, 0.05, 0.15)
    grid_g = (0.05, 0.2, 0.4)
    warmup = 2 * period
    for a in grid_a:
        for b in grid_b:
            for g in grid_g:
                fitted, _ = _hw_fit_forecast(y, period, 1, a, b, g)
                mae = float(np.mean(np.abs(y[warmup:] - fitted[warmup:])))
                if mae < best["mae"]:
                    best = {"alpha": a, "beta": b, "gamma": g, "mae": mae}
    return best


def _backtest_mape(y: np.ndarray, period: int, params: dict, test_len: int = 28) -> float:
    """
    Backtest rolling-origin one-step-ahead sur les `test_len` derniers jours.
    Métrique : WAPE (somme des erreurs / somme des réels) — robuste aux jours
    à faible CA qui font exploser un MAPE classique.
    """
    n = len(y)
    test_len = min(test_len, n - 3 * period)
    if test_len < 7:
        return 15.0
    abs_err, tot_actual = 0.0, 0.0
    for i in range(n - test_len, n):
        train = y[:i]
        if len(train) < 3 * period:
            continue
        _, fc = _hw_fit_forecast(train, period, 1,
                                 params["alpha"], params["beta"], params["gamma"])
        actual = float(y[i])
        abs_err += abs(float(fc[0]) - actual)
        tot_actual += actual
    if tot_actual <= 1:
        return 15.0
    return round(min(abs_err / tot_actual * 100, 60.0), 1)


def forecast_daily_series(values: list[float], horizon: int = 2) -> dict:
    """
    Prévision journalière avec le meilleur moteur disponible.
    Retourne forecast[horizon], intervalle 80%, MAPE backtesté, moteur utilisé.
    """
    y = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0)
    n = len(y)
    if n < 21:  # < 3 semaines : pas assez pour la saisonnalité hebdo
        avg = float(np.mean(y)) if n else 0.0
        return {"forecast": [avg] * horizon, "ci_low": [avg * 0.7] * horizon,
                "ci_high": [avg * 1.3] * horizon, "mape_backtest": 25.0,
                "engine": "mean_fallback", "params": {}}

    if _SF_AVAILABLE:
        try:
            import pandas as pd
            from statsforecast import StatsForecast
            from statsforecast.models import AutoETS
            df = pd.DataFrame({
                "unique_id": "s", "ds": pd.date_range(end=date.today(), periods=n, freq="D"),
                "y": y,
            })
            sf = StatsForecast(models=[AutoETS(season_length=7)], freq="D")
            fc = sf.forecast(df=df, h=horizon, level=[80])
            point = fc["AutoETS"].to_numpy(dtype=float)
            return {
                "forecast": [round(float(v), 2) for v in np.maximum(0, point)],
                "ci_low":   [round(float(v), 2) for v in np.maximum(0, fc["AutoETS-lo-80"].to_numpy(dtype=float))],
                "ci_high":  [round(float(v), 2) for v in fc["AutoETS-hi-80"].to_numpy(dtype=float)],
                "mape_backtest": _backtest_mape(y, 7, {"alpha": .3, "beta": .05, "gamma": .2}),
                "engine": "statsforecast_autoets", "params": {},
            }
        except Exception as e:
            logger.warning("[TS-ENGINE] statsforecast failed (%s) — Holt-Winters numpy", e)

    params = _hw_gridsearch(y, period=7)
    fitted, fc = _hw_fit_forecast(y, 7, horizon,
                                  params["alpha"], params["beta"], params["gamma"])
    mape = _backtest_mape(y, 7, params)
    resid_std = float(np.std(y[14:] - fitted[14:])) if n > 21 else float(np.std(y)) * 0.3
    z80 = 1.282
    return {
        "forecast": [round(float(v), 2) for v in fc],
        "ci_low":   [round(max(0.0, float(v) - z80 * resid_std), 2) for v in fc],
        "ci_high":  [round(float(v) + z80 * resid_std, 2) for v in fc],
        "mape_backtest": mape,
        "engine": "holt_winters_seasonal7",
        "params": {k: params[k] for k in ("alpha", "beta", "gamma")},
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. Accès données — série journalière + profil horaire par DOW
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_daily_series(conn, sid: str, ref_date: date, days: int = 120) -> list[float]:
    """CA journalier continu (jours sans vente = 0) STRICTEMENT avant ref_date."""
    rows = await conn.fetch("""
        SELECT date_only, ca_total FROM sales.vw_ca_par_boutique
        WHERE store_id = $1 AND date_only < $2 AND date_only >= $3
        ORDER BY date_only
    """, sid, ref_date, ref_date - timedelta(days=days))
    by_date = {r["date_only"]: float(r["ca_total"] or 0) for r in rows}
    if not by_date:
        return []
    start = min(by_date)
    series, d = [], start
    while d < ref_date:
        series.append(by_date.get(d, 0.0))
        d += timedelta(days=1)
    return series


async def _fetch_hourly_profile(conn, sid: str, ref_date: date, dow: int,
                                weeks: int = 8) -> dict:
    """
    Profil horaire du jour de semaine `dow` sur `weeks` semaines :
      share[h]  : part moyenne du CA journalier réalisée à l'heure h
      mean[h]   : CA moyen de l'heure h (TND)
      std[h]    : écart-type du CA de l'heure h
    Fallback : PATTERN_HORAIRE global si historique insuffisant.
    """
    rows = await conn.fetch("""
        SELECT date_only, heure, SUM(lig_ttc) AS ca_h
        FROM sales.transactions
        WHERE store_id = $1 AND date_only < $2 AND date_only >= $3
          AND EXTRACT(DOW FROM date_only) = $4
        GROUP BY date_only, heure
    """, sid, ref_date, ref_date - timedelta(weeks=weeks), (dow + 1) % 7)
    # NB : EXTRACT(DOW) postgres : 0=dimanche ; python weekday() : 0=lundi

    per_day: dict[date, dict[int, float]] = {}
    for r in rows:
        h = int(r["heure"] or 0)
        if STORE_OPEN_HOUR <= h <= STORE_CLOSE_HOUR + 1:
            per_day.setdefault(r["date_only"], {})[h] = float(r["ca_h"] or 0)

    hours = list(range(STORE_OPEN_HOUR, STORE_CLOSE_HOUR + 2))
    if len(per_day) < 3:
        total = sum(PATTERN_HORAIRE.values()) or 100.0
        share = {h: PATTERN_HORAIRE.get(h, 0.0) / total for h in hours}
        return {"share": share, "mean": {}, "std": {}, "nb_days": len(per_day),
                "source": "pattern_global_fallback"}

    shares: dict[int, list[float]] = {h: [] for h in hours}
    amounts: dict[int, list[float]] = {h: [] for h in hours}
    for _, day_hours in per_day.items():
        day_total = sum(day_hours.values())
        if day_total <= 0:
            continue
        for h in hours:
            ca_h = day_hours.get(h, 0.0)
            shares[h].append(ca_h / day_total)
            amounts[h].append(ca_h)

    share = {h: float(np.mean(shares[h])) if shares[h] else 0.0 for h in hours}
    s_tot = sum(share.values()) or 1.0
    share = {h: v / s_tot for h, v in share.items()}
    return {
        "share": share,
        "mean": {h: round(float(np.mean(amounts[h])), 2) if amounts[h] else 0.0 for h in hours},
        "std":  {h: round(float(np.std(amounts[h])), 2) if len(amounts[h]) > 1 else 0.0 for h in hours},
        "nb_days": len(per_day),
        "source": f"historical_dow_{weeks}w",
    }


async def _fetch_today_hourly(conn, sid: str, ref_date: date) -> dict[int, float]:
    """CA réel par heure aujourd'hui (transactions batch + temps réel)."""
    rows = await conn.fetch("""
        SELECT heure, SUM(lig_ttc) AS ca_h FROM (
            SELECT heure, lig_ttc FROM sales.transactions
            WHERE store_id = $1 AND date_only = $2
            UNION ALL
            SELECT heure, lig_ttc FROM sales.transactions_rt
            WHERE store_id = $1 AND date_only = $2
        ) c GROUP BY heure ORDER BY heure
    """, sid, ref_date)
    return {int(r["heure"]): float(r["ca_h"] or 0) for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
# 3. Analyse temps réel complète
# ══════════════════════════════════════════════════════════════════════════════

def _cum_share(share: dict[int, float], upto_hour: int) -> float:
    return max(0.02, sum(v for h, v in share.items() if h <= upto_hour))


def _classify_hour(deviation: float, z: float) -> str:
    if deviation <= HOURLY_DEV_ALERT or z <= -HOURLY_Z_ALERT:
        return "ALERT"
    if deviation <= HOURLY_DEV_WATCH or z <= -HOURLY_Z_WATCH:
        return "WATCH"
    if deviation >= 0.25 or z >= HOURLY_Z_ALERT:
        return "OVER"
    return "OK"


def _trend_signal(ledger: list[dict]) -> str:
    """Tendance sur les 3 dernières heures écoulées : ratio réel/attendu croissant ?"""
    recent = [e for e in ledger if e["expected"] > 0][-3:]
    if len(recent) < 2:
        return "UNKNOWN"
    ratios = [e["actual"] / e["expected"] for e in recent]
    delta = ratios[-1] - ratios[0]
    if delta > 0.15:
        return "ACCELERATING"
    if delta < -0.15:
        return "DECELERATING"
    return "STABLE"


async def analyze_store(store_id: str, current_hour: Optional[int] = None) -> dict:
    """
    Point d'entrée unique de l'analyse temps réel.
    Retourne un dict complet : forecast EOD (+CI, MAPE), ledger horaire,
    gaps, prochaines heures, demain, tendance, urgence, faisabilité, résumé FR.
    """
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        ref_date = await _get_business_date(conn, sid)
        if not ref_date:
            return {"error": "no_business_date", "store_id": sid}
        now_h = current_hour if current_hour is not None else datetime.now().hour
        now_h = min(max(now_h, STORE_OPEN_HOUR), STORE_CLOSE_HOUR + 1)
        dow = ref_date.weekday()

        daily_target = await _get_daily_target(conn, sid, ref_date)
        daily_series = await _fetch_daily_series(conn, sid, ref_date)
        profile      = await _fetch_hourly_profile(conn, sid, ref_date, dow)
        today_hourly = await _fetch_today_hourly(conn, sid, ref_date)

        current_ca = round(sum(today_hourly.values()), 2)
        share      = profile["share"]

        # ── Modèle journalier : prévision aujourd'hui (baseline) + demain ────
        daily_fc = forecast_daily_series(daily_series, horizon=2)
        eod_model     = float(daily_fc["forecast"][0])
        tomorrow_fc   = float(daily_fc["forecast"][1])
        mape          = float(daily_fc["mape_backtest"])

        # ── Déroulé intraday : projection depuis le réalisé ──────────────────
        pct_elapsed = _cum_share(share, now_h)
        eod_unfold  = current_ca / pct_elapsed if current_ca > 0 else eod_model

        # Pondération dynamique : tôt le matin on croit le modèle,
        # en fin de journée on croit le déroulé réel.
        w_unfold = min(0.9, pct_elapsed) if current_ca > 0 else 0.0
        eod_blend = round(w_unfold * eod_unfold + (1 - w_unfold) * eod_model, 2)
        eod_blend = max(eod_blend, current_ca)  # l'EOD ne peut pas être < au réalisé

        ci_half = eod_blend * (mape / 100.0) * (1.0 - 0.5 * pct_elapsed) * 1.282
        ci_low  = round(max(current_ca, eod_blend - ci_half), 2)
        ci_high = round(eod_blend + ci_half, 2)

        # ── Ledger horaire : attendu vs réel, heure par heure ───────────────
        # Trajectoire de référence = max(modèle, objectif) : une heure est
        # "en retard" si elle ne suit pas le plan du jour, même quand
        # l'historique récent est faible.
        baseline_day = max(eod_model, daily_target, 1.0)
        ledger: list[dict] = []
        for h in range(STORE_OPEN_HOUR, min(now_h, STORE_CLOSE_HOUR) + 1):
            expected = round(baseline_day * share.get(h, 0.0), 2)
            actual   = round(today_hourly.get(h, 0.0), 2)
            deviation = (actual - expected) / expected if expected > 0 else 0.0
            std_h = float(profile.get("std", {}).get(h, 0.0) or 0.0)
            mean_h = float(profile.get("mean", {}).get(h, expected) or expected)
            z = (actual - mean_h) / std_h if std_h > 1 else 0.0
            ledger.append({
                "hour":       h,
                "expected":   expected,
                "actual":     actual,
                "deviation_pct": round(deviation * 100, 1),
                "z_score":    round(z, 2),
                "status":     _classify_hour(deviation, z),
            })

        gap_hours = [e for e in ledger if e["status"] in ("WATCH", "ALERT")]
        trend     = _trend_signal(ledger)

        # ── Prochaines heures (h+1 … h+3) : attendu ajusté au rythme du jour ─
        day_ratio = eod_blend / baseline_day if baseline_day > 0 else 1.0
        next_hours = [
            {"hour": h,
             "expected_ca": round(baseline_day * share.get(h, 0.0) * day_ratio, 2),
             "share_pct":   round(share.get(h, 0.0) * 100, 1)}
            for h in range(now_h + 1, min(now_h + 4, STORE_CLOSE_HOUR + 1))
        ]

        # ── Gap vs objectif + faisabilité ────────────────────────────────────
        gap_eod    = round(max(0.0, daily_target - eod_blend), 2)
        gap_pct    = round(gap_eod / daily_target * 100, 1) if daily_target > 0 else 0.0
        coverage   = round(min(150.0, eod_blend / daily_target * 100), 1) if daily_target > 0 else 0.0
        attainment = round(current_ca / daily_target * 100, 1) if daily_target > 0 else 0.0

        hours_left = max(0, STORE_CLOSE_HOUR - now_h)
        remaining_needed = max(0.0, daily_target - current_ca)
        ca_h_needed = remaining_needed / hours_left if hours_left > 0 else float("inf")
        # capacité réaliste : p75 historique des heures restantes
        hist_capacity = sum(
            float(profile.get("mean", {}).get(h, 0.0) or 0.0) * 1.25
            for h in range(now_h + 1, STORE_CLOSE_HOUR + 1)
        )
        if remaining_needed <= 0:
            feasibility = "ACHIEVED"
        elif hours_left == 0:
            feasibility = "CLOSED"
        elif hist_capacity >= remaining_needed:
            feasibility = "ACHIEVABLE"
        elif hist_capacity >= remaining_needed * 0.7:
            feasibility = "CHALLENGING"
        else:
            feasibility = "VERY_HARD"

        # ── Urgence composite déterministe ───────────────────────────────────
        time_pressure = min(1.0, max(0.0, (now_h - STORE_OPEN_HOUR) / (STORE_CLOSE_HOUR - STORE_OPEN_HOUR)))
        gap_component = min(1.0, gap_pct / 50.0)
        alert_component = min(0.3, 0.1 * len([e for e in ledger if e["status"] == "ALERT"]))
        trend_component = 0.15 if trend == "DECELERATING" and gap_pct > 10 else 0.0
        urgency_score = round(min(1.0,
            gap_component * 0.45 + time_pressure * 0.20
            + alert_component + trend_component
            + (0.2 if feasibility == "VERY_HARD" else 0.0)), 3)

        if gap_pct > 40 or (coverage < 70 and hours_left <= 4):
            urgency_level = "CRITICAL"
        elif gap_pct > 25 or (coverage < 85 and hours_left <= 3) or feasibility == "VERY_HARD":
            urgency_level = "HIGH"
        elif gap_pct > 10 or trend == "DECELERATING" or gap_hours:
            urgency_level = "MEDIUM"
        else:
            urgency_level = "LOW"

        # ── Résumé français déterministe ─────────────────────────────────────
        summary = _build_summary(
            current_ca, daily_target, eod_blend, ci_low, ci_high, gap_pct,
            attainment, trend, gap_hours, next_hours, feasibility, mape,
            daily_fc["engine"], hours_left,
        )

        return {
            "store_id":       sid,
            "business_date":  str(ref_date),
            "analysis_hour":  now_h,
            "current_ca":     current_ca,
            "daily_target":   daily_target,
            "attainment_pct": attainment,
            # Forecast
            "eod_forecast":   eod_blend,
            "eod_model":      round(eod_model, 2),
            "eod_unfold":     round(eod_unfold, 2),
            "eod_ci_low":     ci_low,
            "eod_ci_high":    ci_high,
            "tomorrow_forecast": round(tomorrow_fc, 2),
            "mape_backtest":  mape,
            "model_engine":   daily_fc["engine"],
            "model_params":   daily_fc["params"],
            "history_days":   len(daily_series),
            "profile_source": profile["source"],
            "profile_days":   profile["nb_days"],
            # Gap
            "gap_eod":        gap_eod,
            "gap_pct":        gap_pct,
            "coverage_pct":   coverage,
            "hours_remaining": hours_left,
            "ca_per_hour_needed": round(ca_h_needed, 2) if ca_h_needed != float("inf") else None,
            "catchup_capacity":   round(hist_capacity, 2),
            "feasibility":    feasibility,
            # Horaire
            "hourly_ledger":  ledger,
            "hourly_gaps":    gap_hours,
            "nb_alert_hours": len([e for e in ledger if e["status"] == "ALERT"]),
            "trend_signal":   trend,
            "next_hours":     next_hours,
            # Urgence
            "urgency_level":  urgency_level,
            "urgency_score":  urgency_score,
            "summary":        summary,
        }
    finally:
        try:
            await conn.close()
        except Exception:
            pass


def _build_summary(ca, target, eod, ci_low, ci_high, gap_pct, attainment,
                   trend, gap_hours, next_hours, feasibility, mape,
                   engine, hours_left) -> str:
    parts = [
        f"CA {ca:.0f}/{target:.0f} TND ({attainment:.0f}% réalisé). "
        f"Prévision EOD {eod:.0f} TND [{ci_low:.0f}–{ci_high:.0f}] "
        f"(modèle {engine}, MAPE {mape:.1f}%)."
    ]
    if gap_pct > 1:
        parts.append(f"Gap projeté {gap_pct:.1f}% vs objectif.")
    else:
        parts.append("Objectif en voie d'être atteint.")
    if gap_hours:
        worst = min(gap_hours, key=lambda e: e["deviation_pct"])
        parts.append(
            f"{len(gap_hours)} heure(s) sous l'attendu — pire : {worst['hour']}h "
            f"({worst['deviation_pct']:+.0f}%, z={worst['z_score']:.1f})."
        )
    trend_fr = {"ACCELERATING": "en accélération", "DECELERATING": "en décélération",
                "STABLE": "stable"}.get(trend)
    if trend_fr:
        parts.append(f"Rythme de vente {trend_fr}.")
    if next_hours:
        nh = next_hours[0]
        parts.append(f"Attendu {nh['hour']}h : {nh['expected_ca']:.0f} TND.")
    feas_fr = {"ACHIEVABLE": "rattrapage réaliste", "CHALLENGING": "rattrapage exigeant",
               "VERY_HARD": "rattrapage très difficile — actions fortes requises",
               "ACHIEVED": "objectif atteint"}.get(feasibility)
    if feas_fr and hours_left > 0:
        parts.append(f"Faisabilité : {feas_fr} ({hours_left}h restantes).")
    return " ".join(parts)
