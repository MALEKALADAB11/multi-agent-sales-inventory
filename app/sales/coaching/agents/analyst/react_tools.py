"""
react_tools.py — Agent Analyste Séries Temporelles · v3.0 Robuste
==================================================================
Outils ReAct pour l'analyse temps réel + historique des ventes Ooredoo.

Architecture d'analyse :
  1. fetch_live_pos              — snapshot POS instantané (CA, TX, heure)
  2. compute_eod_forecast        — prévision EOD multi-méthode (ensemble)
  3. compute_realtime_gap        — gap objectif + urgency scoring
  4. get_intraday_trend          — vélocité + accélération intraday
  5. get_seasonal_context        — contexte saisonnier 3 ans (DOW, mois, events)
  6. get_historical_comparison   — comparaison même DOW sur 4 semaines
  7. get_stock_alerts            — alertes stock critique + impact CA
  8. detect_sales_anomalies      — détection anomalies par z-score horaire [NEW]
  9. compute_ts_decomposition    — décomposition série temporelle STL-like [NEW]
 10. forecast_multi_horizon      — prévision J+1h, J+3h, EOD, demain [NEW]
 11. analyze_product_velocity    — vélocité produit + jours avant rupture [NEW]
"""

import asyncio
import json
import logging
import math
import os
import statistics
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "database": os.getenv("POSTGRES_DB", "ooredoo_sales"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "root"),
}

CLOSING_HOUR = 20
STORE_OPEN_HOUR = 9


async def _conn() -> asyncpg.Connection:
    return await asyncpg.connect(**DB_CONFIG, timeout=8, command_timeout=25)


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 1 — POS temps réel
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def fetch_live_pos(store_id: str) -> str:
    """
    Fetch LIVE POS data for the store.
    Returns: current_ca, nb_transactions, avg_ticket, daily_target,
             current_hour, hours_remaining, performance_pct, gap_tnd.
    Call this first — combines transactions + transactions_rt for complete picture.
    """
    conn = await _conn()
    try:
        today = date.today()
        now_h = datetime.now().hour
        row = await conn.fetchrow("""
            SELECT COALESCE(SUM(lig_ttc), 0) AS ca,
                   COUNT(*)                   AS tx,
                   COALESCE(AVG(lig_ttc), 0)  AS avg_ticket
            FROM (
                SELECT lig_ttc FROM sales.transactions
                WHERE store_id=$1 AND date_only=$2 AND heure <= $3
                UNION ALL
                SELECT lig_ttc FROM sales.transactions_rt
                WHERE store_id=$1 AND date_only=$2 AND date_vente <= NOW()
            ) t
        """, store_id, today, now_h)

        target_row = await conn.fetchrow("""
            SELECT objectif_ca FROM sales.objectifs
            WHERE store_id=$1 AND agent_id IS NULL AND date_objectif=$2
            ORDER BY id DESC LIMIT 1
        """, store_id, today)

        ca         = float(row["ca"] or 0)
        tx         = int(row["tx"] or 0)
        avg_ticket = round(float(row["avg_ticket"] or 0), 2)
        target     = float(target_row["objectif_ca"]) if target_row else 1000.0
        now_h      = datetime.now().hour
        perf_pct   = round(ca / target * 100, 1) if target > 0 else 0

        return _j({
            "current_ca":       round(ca, 2),
            "nb_transactions":  tx,
            "avg_ticket":       avg_ticket,
            "daily_target":     target,
            "current_hour":     now_h,
            "hours_remaining":  max(0, CLOSING_HOUR - now_h),
            "performance_pct":  perf_pct,
            "gap_tnd":          round(max(0, target - ca), 2),
            "snapshot_time":    datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as e:
        logger.warning("[fetch_live_pos] %s: %s", store_id, e)
        return _j({"error": str(e), "current_ca": 0, "daily_target": 1000})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 2 — Prévision EOD multi-méthode (ensemble robuste)
# ══════════════════════════════════════════════════════════════════════════════

_REDIS_FORECAST_TTL = 900


async def _redis_get(key: str) -> dict | None:
    try:
        import redis.asyncio as aioredis, json as _json
        r = await aioredis.from_url(
            f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}",
            decode_responses=True, socket_connect_timeout=1,
        )
        raw = await r.get(key)
        await r.aclose()
        return _json.loads(raw) if raw else None
    except Exception:
        return None


async def _redis_set(key: str, payload: dict, ttl: int) -> None:
    try:
        import redis.asyncio as aioredis, json as _json
        r = await aioredis.from_url(
            f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}",
            decode_responses=True, socket_connect_timeout=1,
        )
        await r.setex(key, ttl, _json.dumps(payload))
        await r.aclose()
    except Exception:
        pass


@tool
async def compute_eod_forecast(store_id: str, current_ca: float, current_hour: int) -> str:
    """
    Compute end-of-day revenue forecast using a 4-method ensemble:
      - linear:    hourly-rate extrapolation (baseline)
      - seasonal:  DOW + month factor from 3-year history
      - velocity:  acceleration-adjusted (detects momentum changes)
      - timesfm:   ML forecast from PostgreSQL (if available)
    Returns ensemble weighted average + 90% confidence interval + MAPE estimate.
    Cached in Redis 15min. Re-call only if CA changed >5%.
    """
    cache_key = f"forecast_eod:{store_id}:{date.today().isoformat()}"
    cached = await _redis_get(cache_key)
    if cached and abs(float(cached.get("current_ca_at_cache", 0)) - current_ca) < current_ca * 0.05:
        return _j({**cached, "_from_cache": True})

    conn = await _conn()
    try:
        today      = date.today()
        now_h      = current_hour or datetime.now().hour
        hours_left = max(0, CLOSING_HOUR - now_h)
        hours_done = max(1, now_h - STORE_OPEN_HOUR)

        # ── Méthode 1 : Linéaire
        hourly_rate = current_ca / hours_done
        eod_linear  = current_ca + hourly_rate * hours_left

        # ── Méthode 2 : Saisonnière (DOW + mois depuis 3 ans)
        dow       = today.weekday()
        month_num = today.month
        cutoff    = today - timedelta(days=1095)

        row_s = await conn.fetchrow("""
            SELECT
                AVG(CASE WHEN day_of_week=$3 THEN quantity_sold END)  AS dow_avg,
                AVG(quantity_sold)                                      AS global_avg,
                AVG(CASE WHEN month_num=$4   THEN quantity_sold END)   AS month_avg
            FROM inventory.sales_history
            WHERE store_id=$1 AND record_date >= $2
        """, store_id, cutoff, dow, month_num)

        if row_s:
            g   = float(row_s["global_avg"] or 1)
            dow_f  = float(row_s["dow_avg"]  or g) / g if g > 0 else 1.0
            mon_f  = float(row_s["month_avg"] or g) / g if g > 0 else 1.0
        else:
            dow_f, mon_f = 1.0, 1.0

        seasonal_factor = (dow_f * mon_f) ** 0.5
        eod_seasonal    = eod_linear * max(0.7, min(seasonal_factor, 1.5))

        # ── Méthode 3 : Vélocité + accélération (dernières 3 heures)
        rows_h = await conn.fetch("""
            SELECT heure, SUM(lig_ttc) AS ca_h
            FROM sales.transactions
            WHERE store_id=$1 AND date_only=$2 AND heure BETWEEN $3 AND $4
            GROUP BY heure
            UNION ALL
            SELECT EXTRACT(HOUR FROM date_vente)::int AS heure, SUM(lig_ttc) AS ca_h
            FROM sales.transactions_rt
            WHERE store_id=$1 AND date_only=$2
              AND EXTRACT(HOUR FROM date_vente) BETWEEN $3 AND $4
            GROUP BY EXTRACT(HOUR FROM date_vente)::int
        """, store_id, today, max(STORE_OPEN_HOUR, now_h - 3), now_h)

        hourly_vals = {}
        for r in rows_h:
            h = int(r["heure"])
            hourly_vals[h] = hourly_vals.get(h, 0) + float(r["ca_h"] or 0)

        sorted_hours = sorted(hourly_vals.items())
        if len(sorted_hours) >= 2:
            ca_recent   = sorted_hours[-1][1]
            ca_prev     = sorted_hours[-2][1]
            accel_ratio = (ca_recent - ca_prev) / max(ca_prev, 1)
            # Ajustement conservateur ±15% max
            velocity_adj = max(-0.15, min(accel_ratio * 0.3, 0.15))
        else:
            velocity_adj = 0.0

        eod_velocity = eod_linear * (1 + velocity_adj)

        # ── Méthode 4 : TimesFM depuis PG
        eod_timesfm = None
        try:
            tfm = await conn.fetchrow("""
                SELECT forecast_eod FROM sales.forecasts_eod
                WHERE store_id=$1 AND forecast_date=$2
                ORDER BY created_at DESC LIMIT 1
            """, store_id, today)
            if tfm:
                eod_timesfm = float(tfm["forecast_eod"] or 0)
        except Exception:
            pass

        # ── Ensemble pondéré
        # Poids : TimesFM > saisonnier > vélocité > linéaire
        # Confiance croît avec les heures écoulées
        hours_confidence = min(1.0, hours_done / 6)  # max confiance après 6h de vente

        if eod_timesfm and eod_timesfm > 0:
            w_l, w_s, w_v, w_t = 0.15, 0.25, 0.20, 0.40
            eod_weighted = (w_l*eod_linear + w_s*eod_seasonal +
                            w_v*eod_velocity + w_t*eod_timesfm)
        else:
            w_l, w_s, w_v = 0.25, 0.40, 0.35
            eod_weighted = w_l*eod_linear + w_s*eod_seasonal + w_v*eod_velocity

        # ── Intervalles de confiance à 90%
        # Élargissement selon le MAPE historique estimé et les heures restantes
        base_uncertainty = 0.12 + (hours_left / CLOSING_HOUR) * 0.15
        ci_half = eod_weighted * base_uncertainty * (1 - hours_confidence * 0.5)
        ci_low  = max(current_ca, eod_weighted - ci_half)
        ci_high = eod_weighted + ci_half

        mape_estimate = round(base_uncertainty * 100, 1)

        payload = {
            "eod_linear":           round(eod_linear),
            "eod_seasonal":         round(eod_seasonal),
            "eod_velocity":         round(eod_velocity),
            "eod_timesfm":          round(eod_timesfm) if eod_timesfm else None,
            "eod_weighted":         round(eod_weighted),
            "ci_low":               round(ci_low),
            "ci_high":              round(ci_high),
            "mape_estimate_pct":    mape_estimate,
            "dow_factor":           round(dow_f, 3),
            "month_factor":         round(mon_f, 3),
            "seasonal_factor":      round(seasonal_factor, 3),
            "velocity_adjustment":  round(velocity_adj * 100, 1),
            "hours_done":           hours_done,
            "hours_remaining":      hours_left,
            "confidence_pct":       round(min(95, 35 + hours_done * 8), 1),
            "current_ca_at_cache":  round(current_ca, 2),
        }
        asyncio.create_task(_redis_set(cache_key, payload, _REDIS_FORECAST_TTL))
        return _j(payload)

    except Exception as e:
        logger.warning("[compute_eod_forecast] %s: %s", store_id, e)
        return _j({"error": str(e), "eod_weighted": current_ca, "ci_low": current_ca, "ci_high": current_ca * 1.5})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 3 — Gap temps réel + urgency
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def compute_realtime_gap(
    current_ca: float,
    eod_forecast: float,
    daily_target: float,
) -> str:
    """
    Calculate real-time gap vs daily target and urgency score.
    Combines current achievement + EOD forecast to give composite urgency.
    Returns: gap_pct, gap_amount, coverage_pct, urgency_level, urgency_score.
    """
    if daily_target <= 0:
        daily_target = 1000.0

    coverage_pct = min(100.0, round(eod_forecast / daily_target * 100, 1))
    gap_amount   = max(0.0, daily_target - eod_forecast)
    gap_pct      = round(gap_amount / daily_target * 100, 1)
    realized_pct = round(current_ca / daily_target * 100, 1)

    now_h      = datetime.now().hour
    hours_left = max(0, CLOSING_HOUR - now_h)

    # Score d'urgence composite : gap + temps restant + tendance
    if gap_pct > 40 or (coverage_pct < 70 and hours_left < 4):
        urgency_level = "CRITICAL"
        urgency_score = min(1.0, 0.85 + gap_pct / 400)
    elif gap_pct > 25 or (coverage_pct < 85 and hours_left < 3):
        urgency_level = "HIGH"
        urgency_score = 0.65 + gap_pct / 400
    elif gap_pct > 10 or (coverage_pct < 95 and hours_left < 2):
        urgency_level = "MEDIUM"
        urgency_score = 0.35 + gap_pct / 200
    else:
        urgency_level = "LOW"
        urgency_score = max(0.05, gap_pct / 100)

    # CA/heure nécessaire pour atteindre l'objectif
    ca_per_hour_needed = gap_amount / hours_left if hours_left > 0 else float("inf")
    ca_per_hour_current = current_ca / max(1, now_h - STORE_OPEN_HOUR)

    return _j({
        "current_ca":           round(current_ca, 2),
        "eod_forecast":         round(eod_forecast, 2),
        "daily_target":         round(daily_target, 2),
        "realized_pct":         realized_pct,
        "gap_amount":           round(gap_amount, 2),
        "gap_pct":              gap_pct,
        "coverage_pct":         coverage_pct,
        "urgency_level":        urgency_level,
        "urgency_score":        round(urgency_score, 3),
        "hours_remaining":      hours_left,
        "ca_per_hour_needed":   round(ca_per_hour_needed, 2) if ca_per_hour_needed != float("inf") else None,
        "ca_per_hour_current":  round(ca_per_hour_current, 2),
        "feasibility":          (
            "ACHIEVABLE"   if ca_per_hour_needed <= ca_per_hour_current * 1.3 else
            "CHALLENGING"  if ca_per_hour_needed <= ca_per_hour_current * 2.0 else
            "VERY_HARD"
        ),
    })


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 4 — Tendance intraday (vélocité + accélération)
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def get_intraday_trend(store_id: str) -> str:
    """
    Analyze intraday sales velocity and acceleration using last 3 hours.
    Detects ACCELERATING / DECELERATING / STABLE trends.
    Also computes historical benchmark for this exact hour (28-day average).
    """
    conn = await _conn()
    try:
        today = date.today()
        now_h = datetime.now().hour

        async def _hourly_ca(h: int) -> float:
            if h < STORE_OPEN_HOUR or h > 19:
                return 0.0
            # Utilise uniquement transactions pour cohérence avec le benchmark historique
            r = await conn.fetchval("""
                SELECT COALESCE(SUM(lig_ttc), 0)
                FROM sales.transactions
                WHERE store_id=$1 AND date_only=$2 AND heure=$3
            """, store_id, today, h)
            return float(r or 0)

        ca_now  = await _hourly_ca(now_h)
        ca_prev = await _hourly_ca(now_h - 1)
        ca_prev2 = await _hourly_ca(now_h - 2)

        velocity     = ca_now
        acceleration = ((ca_now - ca_prev) / max(ca_prev, 1)) * 100 if ca_prev > 0 else 0

        # Tendance sur 3 heures (régression linéaire simple)
        if ca_prev2 > 0 and ca_prev > 0 and ca_now > 0:
            vals = [ca_prev2, ca_prev, ca_now]
            slope = (vals[2] - vals[0]) / 2
            trend_slope_pct = round(slope / max(vals[0], 1) * 100, 1)
        else:
            trend_slope_pct = 0.0

        if acceleration > 15:
            trend_signal = "ACCELERATING"
        elif acceleration < -15:
            trend_signal = "DECELERATING"
        else:
            trend_signal = "STABLE"

        # Benchmark historique : moyenne CA pour cette heure sur 28 jours
        hist_rows = await conn.fetch("""
            SELECT date_only, SUM(lig_ttc) AS ca_h
            FROM sales.transactions
            WHERE store_id=$1 AND heure=$2
              AND date_only >= CURRENT_DATE - INTERVAL '28 days'
              AND date_only < CURRENT_DATE
            GROUP BY date_only
        """, store_id, now_h)

        hist_vals = [float(r["ca_h"]) for r in hist_rows if r["ca_h"]]
        hist_avg  = round(statistics.mean(hist_vals), 2) if hist_vals else 0
        hist_std  = round(statistics.stdev(hist_vals), 2) if len(hist_vals) > 1 else hist_avg * 0.2
        vs_hist   = round((ca_now - hist_avg) / max(hist_avg, 1) * 100, 1) if hist_avg > 0 else 0

        # Z-score de l'heure courante
        z_score = round((ca_now - hist_avg) / max(hist_std, 1), 2) if hist_std > 0 else 0

        peak_hours = [16, 17, 18, 19]
        peak_ahead = [h for h in peak_hours if h > now_h]

        return _j({
            "current_hour":         now_h,
            "ca_this_hour":         round(ca_now, 2),
            "ca_prev_hour":         round(ca_prev, 2),
            "ca_two_hours_ago":     round(ca_prev2, 2),
            "velocity_tnd_per_h":   round(velocity, 2),
            "acceleration_pct":     round(acceleration, 1),
            "trend_slope_3h_pct":   trend_slope_pct,
            "trend_signal":         trend_signal,
            "hist_avg_this_hour":   hist_avg,
            "hist_std_this_hour":   hist_std,
            "vs_historical_pct":    vs_hist,
            "z_score":              z_score,
            "anomaly":              abs(z_score) > 2.0,
            "peak_hours_ahead":     peak_ahead,
        })
    except Exception as e:
        logger.warning("[get_intraday_trend] %s: %s", store_id, e)
        return _j({"error": str(e), "trend_signal": "UNKNOWN"})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 5 — Contexte saisonnier (3 ans d'historique)
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def get_seasonal_context(store_id: str) -> str:
    """
    Get seasonal context from 3-year sales history.
    Returns: dow_factor, month_factor, active_events, expected_uplift_pct.
    Draws on inventory.sales_history (1.49M rows, Jan 2022–Jul 2026).
    """
    conn = await _conn()
    try:
        today = date.today()
        dow   = today.weekday()
        month = today.month

        cutoff_3y = today - timedelta(days=1095)
        row = await conn.fetchrow("""
            SELECT
                AVG(CASE WHEN day_of_week=$2 THEN quantity_sold END)      AS dow_avg,
                AVG(quantity_sold)                                          AS global_avg,
                AVG(CASE WHEN month_num=$3 THEN quantity_sold END)         AS month_avg,
                AVG(CASE WHEN is_event_day=TRUE THEN uplift_factor END)    AS event_uplift,
                STDDEV(quantity_sold)                                       AS global_std
            FROM inventory.sales_history
            WHERE store_id=$1 AND record_date >= $4
        """, store_id, dow, month, cutoff_3y)

        global_avg   = float(row["global_avg"]  or 1)
        global_std   = float(row["global_std"]  or global_avg * 0.3)
        dow_avg      = float(row["dow_avg"]      or global_avg)
        month_avg    = float(row["month_avg"]    or global_avg)
        event_uplift = float(row["event_uplift"] or 1.0)

        dow_factor   = round(dow_avg  / max(global_avg, 1), 3)
        month_factor = round(month_avg / max(global_avg, 1), 3)
        cv           = round(global_std / max(global_avg, 1), 3)  # coefficient de variation

        events = await conn.fetch("""
            SELECT event_name, intensite, scope,
                   ROUND((COALESCE(uplift_terminal,0) + COALESCE(uplift_forfait,0) +
                          COALESCE(uplift_sim,0) + COALESCE(uplift_recharge,0) +
                          COALESCE(uplift_accessoire,0)) / 5.0, 1) AS estimated_uplift_pct
            FROM market.events
            WHERE $1 BETWEEN start_date AND COALESCE(end_date, start_date)
            LIMIT 5
        """, today)

        season_map = {
            (3, 4): "RAMADAN_PRINTEMPS", (4, 5): "EID_FITR",
            (9,):   "RENTREE",           (12,):  "SOLDES_HIVER",
            (6, 7): "ETE",               (1, 2): "SOLDES_ETE",
        }
        season_name = "NORMAL"
        for months, name in season_map.items():
            if month in months:
                season_name = name
                break

        return _j({
            "date":              today.isoformat(),
            "day_of_week":       ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"][dow],
            "dow_factor":        dow_factor,
            "month_factor":      month_factor,
            "expected_uplift_pct": round((dow_factor * month_factor - 1) * 100, 1),
            "event_uplift_factor": round(event_uplift, 3),
            "demand_volatility_cv": cv,
            "active_events":     [dict(e) for e in events],
            "season_name":       season_name,
            "is_high_season":    month_factor > 1.15 or dow_factor > 1.1,
            "data_years":        3,
        })
    except Exception as e:
        logger.warning("[get_seasonal_context] %s: %s", store_id, e)
        return _j({"error": str(e)})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 6 — Comparaison historique même DOW
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def get_historical_comparison(store_id: str) -> str:
    """
    Compare today's performance vs same weekday over the last 4 weeks.
    Returns: avg_same_dow, today_vs_avg_pct, trend_direction, percentile.
    """
    conn = await _conn()
    try:
        today = date.today()
        dow   = today.weekday()
        now_h = datetime.now().hour

        past_dates = [today - timedelta(weeks=w) for w in range(1, 5)]

        values = []
        for d in past_dates:
            val = await conn.fetchval("""
                SELECT COALESCE(SUM(lig_ttc), 0)
                FROM sales.transactions
                WHERE store_id=$1 AND date_only=$2 AND heure <= $3
            """, store_id, d, now_h)
            if val and float(val) > 0:
                values.append(float(val))

        today_ca = await conn.fetchval("""
            SELECT COALESCE(SUM(ca), 0) FROM (
                SELECT lig_ttc AS ca FROM sales.transactions
                WHERE store_id=$1 AND date_only=$2 AND heure<=$3
                UNION ALL
                SELECT lig_ttc FROM sales.transactions_rt
                WHERE store_id=$1 AND date_only=$2
                  AND date_vente <= NOW()
            ) t
        """, store_id, today, now_h)
        today_ca = float(today_ca or 0)

        if values:
            avg_same_dow = statistics.mean(values)
            std_same_dow = statistics.stdev(values) if len(values) > 1 else avg_same_dow * 0.15
            vs_avg_pct   = round((today_ca - avg_same_dow) / max(avg_same_dow, 1) * 100, 1)
            trend        = "ABOVE_AVERAGE" if vs_avg_pct > 5 else ("BELOW_AVERAGE" if vs_avg_pct < -5 else "ON_TRACK")
            # Percentile approximé
            n_below = sum(1 for v in values if v < today_ca)
            percentile = round(n_below / len(values) * 100)
        else:
            avg_same_dow = std_same_dow = 0
            vs_avg_pct = 0
            trend = "NO_HISTORY"
            percentile = 50

        return _j({
            "today_ca_so_far":    round(today_ca, 2),
            "avg_same_dow_ca":    round(avg_same_dow, 2),
            "std_same_dow":       round(std_same_dow, 2),
            "vs_avg_pct":         vs_avg_pct,
            "trend_vs_history":   trend,
            "percentile_rank":    percentile,
            "n_samples":          len(values),
            "current_hour":       now_h,
            "day_name":           ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"][dow],
            "samples":            [round(v, 2) for v in values],
        })
    except Exception as e:
        logger.warning("[get_historical_comparison] %s: %s", store_id, e)
        return _j({"error": str(e)})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 7 — Alertes stock critiques avec impact CA
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def get_stock_alerts(store_id: str) -> str:
    """
    Fetch critical stock alerts — ruptures and low-stock products.
    Links real-time stock levels to sales velocity to estimate revenue at risk.
    Returns: nb_ruptures, nb_critical, revenue_at_risk_tnd, stock_urgency_boost.
    """
    conn = await _conn()
    try:
        try:
            rows = await conn.fetch("""
                SELECT s.sku, s.product_name,
                       COALESCE(s.prix_ttc, 0)    AS prix_ttc,
                       COALESCE(s.stock_dispo, 0)  AS stock_qty,
                       s.stock_risk
                FROM sales.vw_stock_enriched s
                WHERE s.store_id = $1
                  AND (s.stock_risk IN ('rupture', 'critical')
                       OR COALESCE(s.stock_dispo, 0) <= 3)
                ORDER BY COALESCE(s.stock_dispo, 0) ASC
                LIMIT 10
            """, store_id)
        except Exception:
            rows = await conn.fetch("""
                SELECT sl.sku,
                       COALESCE(p.nom, sl.sku::text) AS product_name,
                       COALESCE(p.prix_ttc, 0)       AS prix_ttc,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_qty,
                       CASE
                           WHEN COALESCE(sl.quantity_available, sl.quantity, 0) = 0  THEN 'rupture'
                           WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 3 THEN 'critical'
                           ELSE 'low'
                       END AS stock_risk
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                WHERE sl.store_id = $1
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 5
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC
                LIMIT 10
            """, store_id)

        alerts = [
            {
                "sku":          str(r["sku"]),
                "product_name": str(r["product_name"]),
                "prix_ttc":     float(r["prix_ttc"] or 0),
                "stock_qty":    int(r["stock_qty"]),
                "risk_level":   str(r["stock_risk"]),
            }
            for r in rows
        ]

        nb_ruptures = sum(1 for a in alerts if a["risk_level"] == "rupture")
        nb_critical = sum(1 for a in alerts if a["risk_level"] == "critical")

        # CA à risque : produits en rupture × 2 ventes/jour × prix
        revenue_at_risk = sum(
            a["prix_ttc"] * 2
            for a in alerts if a["risk_level"] == "rupture" and a["prix_ttc"] > 0
        )

        high_value_ruptures = sum(
            1 for a in alerts if a["risk_level"] == "rupture" and a["prix_ttc"] >= 500
        )
        stock_urgency_boost = min(0.25, nb_ruptures * 0.05 + high_value_ruptures * 0.08)

        return _j({
            "nb_ruptures":         nb_ruptures,
            "nb_critical":         nb_critical,
            "nb_low":              len(alerts) - nb_ruptures - nb_critical,
            "revenue_at_risk_tnd": round(revenue_at_risk, 2),
            "stock_urgency_boost": round(stock_urgency_boost, 3),
            "top_alerts":          alerts[:5],
            "summary":             (
                f"{nb_ruptures} rupture(s), {nb_critical} critique(s). "
                f"CA risque: {revenue_at_risk:.0f} TND."
            ) if alerts else "Aucune alerte stock critique.",
        })
    except Exception as e:
        logger.warning("[get_stock_alerts] %s: %s", store_id, e)
        return _j({
            "error": str(e), "nb_ruptures": 0, "nb_critical": 0,
            "revenue_at_risk_tnd": 0, "stock_urgency_boost": 0.0,
            "top_alerts": [], "summary": "Données stock indisponibles.",
        })
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 8 (NEW) — Détection d'anomalies par z-score sur série temporelle
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def detect_sales_anomalies(store_id: str) -> str:
    """
    Detect abnormal sales patterns using statistical z-score analysis on hourly data.
    Compares each hour today vs same-hour distribution over 28 days.
    Anomaly threshold: |z| > 2.0 (top/bottom 2.3% of distribution).
    Returns: anomalies list, overall_anomaly_score, interpretation.
    This tool reveals whether today's pattern is statistically unusual.
    """
    conn = await _conn()
    try:
        today = date.today()
        now_h = datetime.now().hour

        # Benchmark historique par heure (28 derniers jours).
        # On agrège d'abord la somme par (date, heure), puis on fait AVG/STDDEV
        # sur ces sommes journalières — comparable aux sommes horaires d'aujourd'hui.
        hist = await conn.fetch("""
            SELECT heure,
                   AVG(daily_ca)    AS mean_ttc,
                   STDDEV(daily_ca) AS std_ttc,
                   COUNT(*)         AS n_obs
            FROM (
                SELECT date_only, heure, SUM(lig_ttc) AS daily_ca
                FROM sales.transactions
                WHERE store_id=$1
                  AND date_only >= CURRENT_DATE - INTERVAL '28 days'
                  AND date_only < CURRENT_DATE
                  AND heure BETWEEN $2 AND 19
                GROUP BY date_only, heure
            ) dh
            GROUP BY heure
            ORDER BY heure
        """, store_id, STORE_OPEN_HOUR)

        hist_map = {
            int(r["heure"]): {
                "mean": float(r["mean_ttc"] or 0),
                "std":  float(r["std_ttc"]  or 0),
                "n":    int(r["n_obs"]),
            }
            for r in hist
        }

        # Transactions d'aujourd'hui par heure (uniquement depuis transactions)
        # On n'utilise pas transactions_rt ici car elle contient des données simulées
        # qui ne sont pas comparables à la baseline historique.
        today_rows = await conn.fetch("""
            SELECT heure, SUM(lig_ttc) AS ca_h, COUNT(*) AS nb_tx
            FROM sales.transactions
            WHERE store_id=$1 AND date_only=$2 AND heure <= $3
            GROUP BY heure
            ORDER BY heure
        """, store_id, today, now_h)

        anomalies = []
        z_scores   = []

        for r in today_rows:
            h      = int(r["heure"])
            ca_h   = float(r["ca_h"] or 0)
            nb_tx  = int(r["nb_tx"] or 0)
            bench  = hist_map.get(h, {"mean": 0, "std": 1, "n": 0})
            mean_h = bench["mean"]
            std_h  = max(bench["std"], mean_h * 0.15, 1.0)

            z = (ca_h - mean_h) / std_h if std_h > 0 else 0
            z_scores.append(z)

            if abs(z) >= 2.0:
                direction = "SPIKE" if z > 0 else "DIP"
                anomalies.append({
                    "hour":        h,
                    "ca_observed": round(ca_h, 2),
                    "ca_expected": round(mean_h, 2),
                    "z_score":     round(z, 2),
                    "direction":   direction,
                    "severity":    "HIGH" if abs(z) > 3 else "MEDIUM",
                    "nb_tx":       nb_tx,
                    "interpretation": (
                        f"{h}h : ventes {'exceptionnellement élevées' if z > 0 else 'anormalement basses'} "
                        f"(z={z:.1f}, {abs(ca_h-mean_h):.0f} TND vs attendu {mean_h:.0f} TND)"
                    ),
                })

        overall_z   = round(statistics.mean(z_scores), 2) if z_scores else 0
        anomaly_pct = round(len(anomalies) / max(len(z_scores), 1) * 100, 1)

        if abs(overall_z) > 2.0:
            interpretation = f"Journée ATYPIQUE (z={overall_z:.1f}) — pattern inhabituel détecté"
        elif abs(overall_z) > 1.0:
            interpretation = f"Journée légèrement {'au-dessus' if overall_z > 0 else 'en-dessous'} de la normale (z={overall_z:.1f})"
        else:
            interpretation = f"Journée dans la normale (z={overall_z:.1f})"

        return _j({
            "anomalies":           anomalies,
            "nb_anomalies":        len(anomalies),
            "anomaly_hours_pct":   anomaly_pct,
            "overall_z_score":     overall_z,
            "hours_analyzed":      len(z_scores),
            "interpretation":      interpretation,
            "is_atypical_day":     abs(overall_z) > 1.5,
        })
    except Exception as e:
        logger.warning("[detect_sales_anomalies] %s: %s", store_id, e)
        return _j({"error": str(e), "anomalies": [], "interpretation": "Analyse indisponible"})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 9 (NEW) — Décomposition série temporelle STL-like
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def compute_ts_decomposition(store_id: str) -> str:
    """
    Decompose the sales time series into Trend + Weekly Seasonality + Residual.
    Uses 90 days of daily data for robust decomposition.

    Components:
      - trend:     7-day moving average (low-frequency signal)
      - seasonal:  day-of-week pattern (avg DOW / global avg)
      - residual:  actual - trend×seasonal (noise + events)

    Returns: trend_direction (UP/DOWN/FLAT), seasonal_strength,
             residual_volatility, top_seasonal_days, autocorrelation_lag7.
    Use this to understand the underlying dynamics of the store's sales.
    """
    conn = await _conn()
    try:
        # 90 jours de CA journalier
        rows = await conn.fetch("""
            SELECT date_only, SUM(lig_ttc) AS ca_day
            FROM sales.transactions
            WHERE store_id=$1
              AND date_only >= CURRENT_DATE - INTERVAL '90 days'
              AND date_only < CURRENT_DATE
            GROUP BY date_only
            ORDER BY date_only
        """, store_id)

        if len(rows) < 14:
            return _j({"error": "Données insuffisantes pour la décomposition (<14 jours)"})

        dates  = [r["date_only"] for r in rows]
        values = [float(r["ca_day"]) for r in rows]
        n      = len(values)

        # ── Tendance : moyenne mobile 7 jours centrée
        trend = []
        for i in range(n):
            lo = max(0, i - 3)
            hi = min(n, i + 4)
            trend.append(statistics.mean(values[lo:hi]))

        # ── Saisonnalité journalière : moyenne par DOW normalisée
        dow_sums   = [0.0] * 7
        dow_counts = [0]   * 7
        for i, d in enumerate(dates):
            dow = d.weekday()
            dow_sums[dow]   += values[i] / max(trend[i], 1)
            dow_counts[dow] += 1

        global_mean = statistics.mean(values)
        dow_factors = [
            (dow_sums[d] / dow_counts[d]) if dow_counts[d] > 0 else 1.0
            for d in range(7)
        ]
        # Normaliser les facteurs DOW
        factor_mean  = statistics.mean(dow_factors)
        dow_factors  = [f / max(factor_mean, 0.01) for f in dow_factors]

        # ── Résiduel : actual / (trend × seasonal)
        residuals = []
        for i, d in enumerate(dates):
            expected = trend[i] * dow_factors[d.weekday()]
            resid    = (values[i] - expected) / max(expected, 1)
            residuals.append(resid)

        # ── Métriques de la décomposition
        # Tendance (pente linéaire sur la tendance)
        if len(trend) >= 14:
            recent_trend  = statistics.mean(trend[-7:])
            earlier_trend = statistics.mean(trend[-14:-7])
            trend_change  = (recent_trend - earlier_trend) / max(earlier_trend, 1) * 100
            trend_dir = "UP" if trend_change > 3 else ("DOWN" if trend_change < -3 else "FLAT")
        else:
            trend_change, trend_dir = 0.0, "FLAT"

        # Force saisonnière : variance DOW / variance totale
        seasonal_variance = statistics.variance(dow_factors) if len(dow_factors) > 1 else 0
        total_variance    = statistics.variance(values) / max(global_mean**2, 1)
        seasonal_strength = min(1.0, seasonal_variance / max(total_variance, 0.001))

        # Volatilité résiduelle
        resid_std = statistics.stdev(residuals) if len(residuals) > 1 else 0

        # Autocorrélation lag-7 (saisonnalité hebdomadaire)
        if n >= 14:
            x = values[:n-7]
            y = values[7:]
            mx, my = statistics.mean(x), statistics.mean(y)
            num  = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
            den  = (sum((xi-mx)**2 for xi in x) * sum((yi-my)**2 for yi in y)) ** 0.5
            acf7 = round(num / den, 3) if den > 0 else 0
        else:
            acf7 = 0

        # Meilleurs jours de la semaine
        dow_names = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]
        ranked_days = sorted(
            [(dow_names[d], round(dow_factors[d], 3)) for d in range(7)],
            key=lambda x: x[1], reverse=True
        )

        return _j({
            "period_days":           n,
            "global_mean_ca":        round(global_mean, 2),
            "trend_direction":       trend_dir,
            "trend_change_7d_pct":   round(trend_change, 1),
            "recent_trend_avg":      round(statistics.mean(trend[-7:]), 2),
            "seasonal_strength":     round(seasonal_strength, 3),
            "residual_volatility":   round(resid_std, 3),
            "autocorrelation_lag7":  acf7,
            "weekly_seasonality":    {dow_names[d]: round(dow_factors[d], 3) for d in range(7)},
            "best_sales_days":       ranked_days[:3],
            "worst_sales_days":      ranked_days[-3:][::-1],
            "interpretation": (
                f"Tendance {trend_dir} ({trend_change:+.1f}%/semaine). "
                f"Saisonnalité {'forte' if seasonal_strength > 0.3 else 'modérée' if seasonal_strength > 0.1 else 'faible'} "
                f"(force={seasonal_strength:.2f}). "
                f"ACF lag-7={acf7:.2f} — "
                f"{'forte autocorrélation hebdomadaire' if acf7 > 0.5 else 'pattern hebdomadaire modéré'}."
            ),
        })
    except Exception as e:
        logger.warning("[compute_ts_decomposition] %s: %s", store_id, e)
        return _j({"error": str(e)})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 10 (NEW) — Prévision multi-horizon avec intervalles de confiance
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def forecast_multi_horizon(store_id: str, current_ca: float, current_hour: int) -> str:
    """
    Generate forecasts for 4 time horizons using different statistical methods:
      - next_1h:   AR(1) sur données intraday (régression 1ère ordre)
      - next_3h:   extrapolation saisonnière + tendance intraday
      - eod:       ensemble multi-méthode (déjà dans compute_eod_forecast)
      - tomorrow:  DOW factor × moyenne historique J+1
    All forecasts come with 80% confidence intervals.
    Use this when you need to plan actions across multiple time horizons.
    """
    conn = await _conn()
    try:
        today  = date.today()
        now_h  = current_hour or datetime.now().hour

        # ── Données intraday aujourd'hui (CA par heure, source transactions uniquement)
        # transactions_rt contient des données simulées non comparables à la baseline
        today_rows = await conn.fetch("""
            SELECT heure, SUM(lig_ttc) AS ca_h
            FROM sales.transactions
            WHERE store_id=$1 AND date_only=$2 AND heure <= $3
            GROUP BY heure ORDER BY heure
        """, store_id, today, now_h)

        hourly_today = {int(r["heure"]): float(r["ca_h"]) for r in today_rows}

        # Utiliser le CA réel de la DB comme source de vérité
        ca_from_db = sum(hourly_today.values())
        ca_effective = max(current_ca, ca_from_db)  # le plus grand des deux
        hours_done = max(1, now_h - STORE_OPEN_HOUR)
        hours_left = max(0, CLOSING_HOUR - now_h)

        # ── AR(1) sur les heures récentes → prédiction prochaine heure
        # On utilise les 3 dernières heures complètes (exclut l'heure en cours si partielle)
        completed_hours = sorted(h for h in hourly_today if h < now_h)
        recent_3 = [hourly_today.get(h, 0) for h in completed_hours[-3:]] if completed_hours else []

        if len(recent_3) >= 2:
            # Coefficient AR(1) estimé sur les 28 derniers jours (CORR sur sommes horaires)
            hist_ar = await conn.fetchval("""
                SELECT CORR(ca_h, lag_ca_h) FROM (
                    SELECT heure, SUM(lig_ttc) AS ca_h,
                           LAG(SUM(lig_ttc)) OVER (PARTITION BY date_only ORDER BY heure) AS lag_ca_h
                    FROM sales.transactions
                    WHERE store_id=$1 AND date_only >= CURRENT_DATE - INTERVAL '28 days'
                    GROUP BY date_only, heure
                ) t WHERE lag_ca_h IS NOT NULL
            """, store_id)
            phi = min(0.85, max(0.3, float(hist_ar or 0.55)))
            ca_last_completed = recent_3[-1]
            mean_recent = statistics.mean(recent_3)
            next_1h_point = max(0, ca_last_completed * phi + mean_recent * (1 - phi))
        else:
            phi = 0.55
            # Fallback : débit moyen par heure × 1 heure
            next_1h_point = ca_effective / max(hours_done, 1)

        # ── Profil horaire historique — utilise les SOMMES journalières agrégées
        hist_hourly = await conn.fetch("""
            SELECT heure,
                   AVG(daily_ca) AS mean_h,
                   STDDEV(daily_ca) AS std_h
            FROM (
                SELECT date_only, heure, SUM(lig_ttc) AS daily_ca
                FROM sales.transactions
                WHERE store_id=$1
                  AND date_only >= CURRENT_DATE - INTERVAL '28 days'
                  AND date_only < CURRENT_DATE
                  AND heure BETWEEN $2 AND $3
                GROUP BY date_only, heure
            ) dh
            GROUP BY heure
        """, store_id, now_h+1, min(now_h+3, 19))

        hist_profile = {int(r["heure"]): {
            "mean": float(r["mean_h"] or 0),
            "std":  float(r["std_h"]  or 0),
        } for r in hist_hourly}

        next_3h_hours = [h for h in range(now_h+1, min(now_h+4, 20))]
        next_3h_point = sum(hist_profile.get(h, {}).get("mean", 0) for h in next_3h_hours)
        next_3h_std   = math.sqrt(sum(hist_profile.get(h, {}).get("std", 0)**2 for h in next_3h_hours)) if next_3h_hours else 0

        # ── EOD (linéaire + profil saisonnier avec sommes journalières)
        rate       = ca_effective / hours_done
        eod_linear = ca_effective + rate * hours_left

        remaining_hist = await conn.fetch("""
            SELECT heure, AVG(daily_ca) AS mean_h
            FROM (
                SELECT date_only, heure, SUM(lig_ttc) AS daily_ca
                FROM sales.transactions
                WHERE store_id=$1
                  AND date_only >= CURRENT_DATE - INTERVAL '28 days'
                  AND date_only < CURRENT_DATE
                  AND heure > $2 AND heure < $3
                GROUP BY date_only, heure
            ) dh
            GROUP BY heure
        """, store_id, now_h, CLOSING_HOUR)

        eod_seasonal_add = sum(float(r["mean_h"] or 0) for r in remaining_hist)
        eod_seasonal = ca_effective + eod_seasonal_add
        eod_point    = 0.5 * eod_linear + 0.5 * eod_seasonal

        # ── Demain (même DOW, historique 4 semaines)
        tomorrow     = today + timedelta(days=1)
        tomorrow_dow = tomorrow.weekday()
        past_same_dow = await conn.fetch("""
            SELECT SUM(lig_ttc) AS ca_day
            FROM sales.transactions
            WHERE store_id=$1
              AND EXTRACT(DOW FROM date_only) = $2
              AND date_only >= CURRENT_DATE - INTERVAL '28 days'
              AND date_only < CURRENT_DATE
            GROUP BY date_only
            ORDER BY date_only DESC
            LIMIT 4
        """, store_id, tomorrow_dow)

        tmrw_vals = [float(r["ca_day"]) for r in past_same_dow if r["ca_day"]]
        tmrw_point = statistics.mean(tmrw_vals) if tmrw_vals else eod_point
        tmrw_std   = statistics.stdev(tmrw_vals) if len(tmrw_vals) > 1 else tmrw_point * 0.2

        def _ci(point, std_or_pct, z=1.28):
            """80% CI : z=1.28"""
            half = std_or_pct if isinstance(std_or_pct, float) and std_or_pct < point else point * std_or_pct
            return round(max(0, point - z*half), 2), round(point + z*half, 2)

        next_1h_ci  = _ci(next_1h_point, next_1h_point * 0.25)
        next_3h_ci  = _ci(next_3h_point, next_3h_std)
        eod_ci      = _ci(eod_point, eod_point * 0.15)
        tmrw_ci     = _ci(tmrw_point, tmrw_std)

        return _j({
            "store_id":    store_id,
            "as_of":       f"{now_h}:00",
            "current_ca":  round(current_ca, 2),
            "forecasts": {
                "next_1h": {
                    "horizon":    "Prochaine heure",
                    "point":      round(next_1h_point, 2),
                    "ci_80_low":  next_1h_ci[0],
                    "ci_80_high": next_1h_ci[1],
                    "method":     f"AR(1) phi={phi:.2f}",
                },
                "next_3h": {
                    "horizon":    "3 prochaines heures",
                    "point":      round(next_3h_point, 2),
                    "ci_80_low":  next_3h_ci[0],
                    "ci_80_high": next_3h_ci[1],
                    "method":     "Profil horaire historique 28j",
                },
                "eod": {
                    "horizon":    "Fin de journée",
                    "point":      round(eod_point, 2),
                    "ci_80_low":  eod_ci[0],
                    "ci_80_high": eod_ci[1],
                    "method":     "Ensemble linéaire+saisonnier",
                },
                "tomorrow": {
                    "horizon":    "Demain (même DOW)",
                    "point":      round(tmrw_point, 2),
                    "ci_80_low":  tmrw_ci[0],
                    "ci_80_high": tmrw_ci[1],
                    "method":     "Moyenne 4 semaines même DOW",
                    "day_name":   ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"][tomorrow_dow],
                },
            },
        })
    except Exception as e:
        logger.warning("[forecast_multi_horizon] %s: %s", store_id, e)
        return _j({"error": str(e)})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 11 (NEW) — Vélocité produit + jours avant rupture (lien vente→stock)
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def analyze_product_velocity(store_id: str) -> str:
    """
    Analyze real-time product velocity (units/day) and estimate days to stockout.
    Links sales rate (from transactions) to current stock levels (stock_levels).
    This is the CORE real-time sales→stock intelligence tool.

    Returns for each top product:
      - velocity_7d:     unités/jour sur les 7 derniers jours
      - velocity_today:  unités vendues aujourd'hui (temps réel)
      - stock_available: stock actuel (stock_levels, mis à jour par ventes RT)
      - days_to_stockout: stock_available / velocity_7d
      - reorder_urgency: IMMEDIATE / HIGH / MEDIUM / OK
      - revenue_at_risk: si rupture demain, CA perdu estimé
    """
    conn = await _conn()
    try:
        today   = date.today()
        cutoff7 = today - timedelta(days=7)

        # Top produits vendus + stock actuel en jointure cross-schema
        rows = await conn.fetch("""
            SELECT
                t.sku,
                COALESCE(p.nom, t.sku::text)                         AS product_name,
                COALESCE(p.prix_ttc, 0)                              AS prix_ttc,
                COALESCE(p.categorie, 'N/A')                         AS categorie,
                SUM(t.quantity)                                       AS qty_7d,
                COUNT(DISTINCT t.date_only)                          AS days_active,
                COALESCE(sl.quantity_available, sl.quantity, 0)      AS stock_current,
                COALESCE(sl.last_sold, sl.updated_at)                AS last_sold_at
            FROM sales.transactions t
            LEFT JOIN sales.produits p ON p.sku = t.sku
            LEFT JOIN inventory.stock_levels sl
                   ON sl.sku = t.sku AND sl.store_id = t.store_id
            WHERE t.store_id = $1
              AND t.date_only >= $2
              AND t.lig_ttc > 0
            GROUP BY t.sku, p.nom, p.prix_ttc, p.categorie,
                     sl.quantity_available, sl.quantity, sl.last_sold, sl.updated_at
            HAVING SUM(t.quantity) > 0
            ORDER BY qty_7d DESC
            LIMIT 20
        """, store_id, cutoff7)

        # Ventes RT d'aujourd'hui par SKU
        today_rows = await conn.fetch("""
            SELECT cod_prod AS sku, SUM(qte_produit) AS qty_today
            FROM sales.transactions_rt
            WHERE store_id=$1 AND date_only=$2
            GROUP BY cod_prod
        """, store_id, today)
        today_sales = {int(r["sku"]): int(r["qty_today"] or 0) for r in today_rows}

        products = []
        for r in rows:
            sku_int      = int(r["sku"])
            qty_7d       = int(r["qty_7d"]  or 0)
            days_active  = int(r["days_active"] or 1)
            velocity_7d  = round(qty_7d / max(days_active, 1), 2)
            stock_cur    = int(r["stock_current"] or 0)
            prix         = float(r["prix_ttc"] or 0)
            qty_today    = today_sales.get(sku_int, 0)

            # Jours avant rupture avec la vélocité actuelle
            if velocity_7d > 0 and stock_cur > 0:
                days_to_stockout = round(stock_cur / velocity_7d, 1)
            elif stock_cur == 0:
                days_to_stockout = 0.0
            else:
                days_to_stockout = 999.0

            # Urgence réassort
            if days_to_stockout == 0:
                urgency = "RUPTURE"
            elif days_to_stockout <= 2:
                urgency = "IMMEDIATE"
            elif days_to_stockout <= 5:
                urgency = "HIGH"
            elif days_to_stockout <= 14:
                urgency = "MEDIUM"
            else:
                urgency = "OK"

            revenue_at_risk = round(velocity_7d * prix, 2) if urgency in ("RUPTURE", "IMMEDIATE") else 0

            products.append({
                "sku":             str(sku_int),
                "product_name":    str(r["product_name"]),
                "categorie":       str(r["categorie"]),
                "prix_ttc":        round(prix, 2),
                "qty_sold_7d":     qty_7d,
                "qty_sold_today":  qty_today,
                "velocity_7d":     velocity_7d,
                "stock_available": stock_cur,
                "days_to_stockout": days_to_stockout,
                "reorder_urgency": urgency,
                "revenue_at_risk": revenue_at_risk,
            })

        # Synthèse
        urgent = [p for p in products if p["reorder_urgency"] in ("RUPTURE","IMMEDIATE","HIGH")]
        total_risk = sum(p["revenue_at_risk"] for p in products)

        return _j({
            "store_id":          store_id,
            "analysis_date":     today.isoformat(),
            "products":          products,
            "nb_products":       len(products),
            "nb_urgent":         len(urgent),
            "total_revenue_risk_tnd": round(total_risk, 2),
            "top_velocity":      products[:3] if products else [],
            "most_urgent":       sorted(urgent, key=lambda x: x["days_to_stockout"])[:3],
            "summary": (
                f"{len(urgent)} produit(s) en urgence réassort sur {len(products)} analysés. "
                f"CA à risque : {total_risk:.0f} TND/jour."
            ) if products else "Aucune donnée produit disponible.",
        })
    except Exception as e:
        logger.warning("[analyze_product_velocity] %s: %s", store_id, e)
        return _j({"error": str(e), "products": [], "summary": "Analyse indisponible."})
    finally:
        await conn.close()


@tool
async def get_purchase_orders_kanban(store_id: str) -> str:
    """
    Fetch the supply Kanban board — purchase orders in flight for this store,
    grouped by statut (SUGGERE, BROUILLON, SOUMIS, CONFIRME, EXPEDIE, RECU...).
    Use it to know whether a rupture/critical SKU already has a replenishment
    order pending (avoid recommending push on products with no incoming stock,
    or flagging a rupture that is already being resolved).

    Cross-module call: goes through the inventory MCP server (async stdio),
    per the mcp_wrappers.py architecture note — sales never touches the
    supply schema directly.
    """
    try:
        from app.inventory.tools.internal.mcp_wrappers import MCP_SERVER_PATH
        from app.inventory.integrations.mcp_client import InventoryMCPClient

        client = InventoryMCPClient(MCP_SERVER_PATH)
        async with client.connect():
            return await client.call_tool(
                "list_purchase_orders", {"store_id": store_id}
            )
    except Exception as exc:
        logger.warning("get_purchase_orders_kanban(%s): %s", store_id, exc)
        return _j({"error": f"Kanban PO indisponible: {exc}"})


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════════════════

REACT_ANALYST_TOOLS = [
    fetch_live_pos,
    compute_eod_forecast,
    compute_realtime_gap,
    get_intraday_trend,
    get_seasonal_context,
    get_historical_comparison,
    get_stock_alerts,
    detect_sales_anomalies,
    compute_ts_decomposition,
    forecast_multi_horizon,
    analyze_product_velocity,
    get_purchase_orders_kanban,
]
