"""
react_tools.py — Outils ReAct pour l'Agent Analyste Séries Temporelles.

6 outils que le LLM peut appeler dynamiquement :
  1. fetch_live_pos          — POS en temps réel (CA, TX, heure)
  2. compute_eod_forecast     — Prévision EOD multi-méthode (linéaire + saisonnier + TimesFM)
  3. compute_realtime_gap     — Gap vs target + urgency_score
  4. get_intraday_trend       — Vélocité et accélération intraday
  5. get_seasonal_context     — Contexte saisonnier 3 ans (DOW, mois, événements)
  6. get_historical_comparison — Comparaison avec même DOW semaines passées
"""

import json
import logging
import math
import os
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
    "password": os.getenv("POSTGRES_PASSWORD", "admin"),
}

CLOSING_HOUR = 20


async def _conn() -> asyncpg.Connection:
    return await asyncpg.connect(**DB_CONFIG, timeout=8, command_timeout=20)


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 1 — POS temps réel
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def fetch_live_pos(store_id: str) -> str:
    """
    Fetch LIVE POS data for the store.
    Returns: current_ca (TND), nb_transactions, avg_ticket, daily_target,
             current_hour, hours_remaining, snapshot_time.
    Call this first — and again mid-loop if you need the freshest figure.
    """
    conn = await _conn()
    try:
        today = date.today()
        row = await conn.fetchrow("""
            SELECT COALESCE(SUM(lig_ttc), 0) AS ca,
                   COUNT(*)                   AS tx,
                   AVG(lig_ttc)               AS avg_ticket
            FROM (
                SELECT lig_ttc FROM sales.transactions    WHERE store_id=$1 AND date_only=$2
                UNION ALL
                SELECT lig_ttc FROM sales.transactions_rt WHERE store_id=$1 AND date_only=$2
            ) t
        """, store_id, today)

        target_row = await conn.fetchrow("""
            SELECT objectif_ca FROM sales.objectifs
            WHERE store_id=$1 AND agent_id IS NULL AND date_objectif=$2
        """, store_id, today)

        ca         = float(row["ca"] or 0)
        tx         = int(row["tx"] or 0)
        avg_ticket = round(float(row["avg_ticket"] or 0), 2)
        target     = float(target_row["objectif_ca"]) if target_row else 1007.0
        now_h      = datetime.now().hour

        return _j({
            "current_ca":       round(ca, 2),
            "nb_transactions":  tx,
            "avg_ticket":       avg_ticket,
            "daily_target":     target,
            "current_hour":     now_h,
            "hours_remaining":  max(0, CLOSING_HOUR - now_h),
            "snapshot_time":    datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as e:
        logger.warning("[react_tool/fetch_live_pos] %s: %s", store_id, e)
        return _j({"error": str(e), "current_ca": 0, "daily_target": 1007})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 2 — Prévision EOD multi-méthode
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def compute_eod_forecast(store_id: str, current_ca: float, current_hour: int) -> str:
    """
    Compute end-of-day revenue forecast using 3 methods:
      - linear:   simple hourly-rate extrapolation
      - seasonal: adjusted by today's DOW + month factor from 3-year sales_history
      - timesfm:  ML forecast stored in PostgreSQL (if available)
    Returns: eod_linear, eod_seasonal, eod_weighted (best estimate), confidence_pct.
    """
    conn = await _conn()
    try:
        today    = date.today()
        now_h    = current_hour or datetime.now().hour
        hours_left = max(0, CLOSING_HOUR - now_h)
        hours_done = max(1, now_h - 9)  # store opens at 9h

        # ── Linear
        hourly_rate = current_ca / max(hours_done, 1)
        eod_linear  = current_ca + hourly_rate * hours_left

        # ── Seasonal: DOW + month factors from 3-year sales_history
        dow       = today.weekday()   # 0=Mon … 6=Sun
        month_num = today.month

        cutoff = today - timedelta(days=365)
        row_seasonal = await conn.fetchrow("""
            SELECT
                AVG(CASE WHEN day_of_week=$3 THEN quantity_sold END)  AS dow_avg,
                AVG(quantity_sold)                                      AS global_avg,
                AVG(CASE WHEN month_num=$4 THEN quantity_sold END)     AS month_avg
            FROM inventory.sales_history
            WHERE store_id=$1
              AND record_date >= $2
        """, store_id, cutoff, dow, month_num)

        dow_factor   = 1.0
        month_factor = 1.0
        if row_seasonal:
            global_avg = float(row_seasonal["global_avg"] or 1)
            if global_avg > 0:
                if row_seasonal["dow_avg"]:
                    dow_factor = float(row_seasonal["dow_avg"]) / global_avg
                if row_seasonal["month_avg"]:
                    month_factor = float(row_seasonal["month_avg"]) / global_avg

        seasonal_factor = (dow_factor * month_factor) ** 0.5  # geometric mean
        eod_seasonal = eod_linear * max(0.7, min(seasonal_factor, 1.5))

        # ── TimesFM: ML forecast from PostgreSQL if available
        eod_timesfm = None
        try:
            tfm_row = await conn.fetchrow("""
                SELECT forecast_eod FROM sales.forecasts_eod
                WHERE store_id=$1 AND forecast_date=$2
                ORDER BY created_at DESC LIMIT 1
            """, store_id, today)
            if tfm_row:
                eod_timesfm = float(tfm_row["forecast_eod"] or 0)
        except Exception:
            pass

        # ── Weighted average
        if eod_timesfm and eod_timesfm > 0:
            eod_weighted = 0.4 * eod_linear + 0.3 * eod_seasonal + 0.3 * eod_timesfm
        else:
            eod_weighted = 0.5 * eod_linear + 0.5 * eod_seasonal

        # Confidence: higher when more hours elapsed
        confidence_pct = min(95, 40 + hours_done * 5)

        return _j({
            "eod_linear":       round(eod_linear),
            "eod_seasonal":     round(eod_seasonal),
            "eod_timesfm":      round(eod_timesfm) if eod_timesfm else None,
            "eod_weighted":     round(eod_weighted),
            "dow_factor":       round(dow_factor, 3),
            "month_factor":     round(month_factor, 3),
            "seasonal_factor":  round(seasonal_factor, 3),
            "confidence_pct":   confidence_pct,
            "hours_remaining":  hours_left,
        })
    except Exception as e:
        logger.warning("[react_tool/compute_eod_forecast] %s: %s", store_id, e)
        return _j({"error": str(e), "eod_weighted": current_ca})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 3 — Gap en temps réel
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def compute_realtime_gap(
    current_ca: float,
    eod_forecast: float,
    daily_target: float,
) -> str:
    """
    Calculate REAL-TIME gap vs daily target and urgency score.
    Uses current_ca (already achieved) and eod_forecast (what we expect to finish at).
    Returns: gap_pct, gap_amount, coverage_pct, urgency_level, urgency_score.
    """
    if daily_target <= 0:
        daily_target = 1007.0

    coverage_pct = min(100.0, round(eod_forecast / daily_target * 100, 1))
    gap_amount   = max(0.0, daily_target - eod_forecast)
    gap_pct      = round(gap_amount / daily_target * 100, 1)

    hours_now = datetime.now().hour
    hours_left = max(0, CLOSING_HOUR - hours_now)

    # Urgency scoring
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

    return _j({
        "current_ca":     round(current_ca, 2),
        "eod_forecast":   round(eod_forecast, 2),
        "daily_target":   round(daily_target, 2),
        "gap_amount":     round(gap_amount, 2),
        "gap_pct":        gap_pct,
        "coverage_pct":   coverage_pct,
        "urgency_level":  urgency_level,
        "urgency_score":  round(urgency_score, 3),
        "hours_remaining": hours_left,
    })


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 4 — Tendance intraday
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def get_intraday_trend(store_id: str) -> str:
    """
    Analyze intraday sales velocity and acceleration.
    Compares last 2 hours of TX to detect acceleration/deceleration.
    Returns: velocity_tnd_per_hour, tx_per_hour, acceleration_pct,
             trend_signal (ACCELERATING/DECELERATING/STABLE), peak_hour_ahead.
    """
    conn = await _conn()
    try:
        today  = date.today()
        now_h  = datetime.now().hour
        prev_h = now_h - 1

        async def _hourly_ca(h: int) -> float:
            if h < 9 or h > 19:
                return 0.0
            r = await conn.fetchval("""
                SELECT COALESCE(SUM(lig_ttc), 0)
                FROM (
                    SELECT lig_ttc FROM sales.transactions
                    WHERE store_id=$1 AND date_only=$2 AND heure=$3
                    UNION ALL
                    SELECT lig_ttc FROM sales.transactions_rt
                    WHERE store_id=$1 AND date_only=$2 AND heure=$3
                ) t
            """, store_id, today, h)
            return float(r or 0)

        ca_now  = await _hourly_ca(now_h)
        ca_prev = await _hourly_ca(prev_h)

        velocity     = ca_now  # TND this hour
        acceleration = ((ca_now - ca_prev) / max(ca_prev, 1)) * 100 if ca_prev > 0 else 0

        if acceleration > 15:
            trend_signal = "ACCELERATING"
        elif acceleration < -15:
            trend_signal = "DECELERATING"
        else:
            trend_signal = "STABLE"

        # Known peak hours for Ooredoo stores
        peak_hours = [16, 17, 18, 19]
        peak_ahead = [h for h in peak_hours if h > now_h]

        # Historical average for this hour
        hist = await conn.fetchval("""
            SELECT AVG(ca_heure) FROM (
                SELECT SUM(lig_ttc) AS ca_heure
                FROM sales.transactions
                WHERE store_id=$1 AND heure=$2
                  AND date_only >= CURRENT_DATE - INTERVAL '28 days'
                GROUP BY date_only
            ) sub
        """, store_id, now_h)
        hist_avg_h = round(float(hist or 0), 2)

        return _j({
            "current_hour":        now_h,
            "ca_this_hour":        round(ca_now, 2),
            "ca_prev_hour":        round(ca_prev, 2),
            "velocity_tnd_per_h":  round(velocity, 2),
            "acceleration_pct":    round(acceleration, 1),
            "trend_signal":        trend_signal,
            "historical_avg_h":    hist_avg_h,
            "vs_historical_pct":   round((ca_now - hist_avg_h) / max(hist_avg_h, 1) * 100, 1),
            "peak_hours_ahead":    peak_ahead,
        })
    except Exception as e:
        logger.warning("[react_tool/get_intraday_trend] %s: %s", store_id, e)
        return _j({"error": str(e), "trend_signal": "UNKNOWN"})
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OUTIL 5 — Contexte saisonnier (3 ans d'historique)
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def get_seasonal_context(store_id: str) -> str:
    """
    Get seasonal context for today from 3-year sales history.
    Returns: dow_factor, month_factor, active_events, expected_uplift_pct,
             season_name, is_high_season.
    Draws on inventory.sales_history (1.49M rows, Jan 2022–Jul 2026).
    """
    conn = await _conn()
    try:
        today = date.today()
        dow   = today.weekday()
        month = today.month

        # DOW factor from historical: avg(DOW_X) / avg(all_days)
        row = await conn.fetchrow("""
            SELECT
                AVG(CASE WHEN day_of_week=$2 THEN quantity_sold END)  AS dow_avg,
                AVG(quantity_sold)                                      AS global_avg,
                AVG(CASE WHEN month_num=$3 THEN quantity_sold END)     AS month_avg,
                AVG(CASE WHEN is_event_day=TRUE THEN uplift_factor END) AS event_uplift
            FROM inventory.sales_history
            WHERE record_date >= $1 - INTERVAL '3 years'
        """, today, dow, month)

        global_avg   = float(row["global_avg"]  or 1)
        dow_avg      = float(row["dow_avg"]      or global_avg)
        month_avg    = float(row["month_avg"]    or global_avg)
        event_uplift = float(row["event_uplift"] or 1.0)

        dow_factor   = round(dow_avg  / max(global_avg, 1), 3)
        month_factor = round(month_avg / max(global_avg, 1), 3)

        # Active market events today
        events = await conn.fetch("""
            SELECT event_name, intensite, estimated_uplift_pct, scope
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

        is_high = month_factor > 1.15 or dow_factor > 1.1

        return _j({
            "date":             today.isoformat(),
            "day_of_week":      ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"][dow],
            "dow_factor":       dow_factor,
            "month_factor":     month_factor,
            "expected_uplift_pct": round((dow_factor * month_factor - 1) * 100, 1),
            "event_uplift_factor": round(event_uplift, 3),
            "active_events":    [dict(e) for e in events],
            "season_name":      season_name,
            "is_high_season":   is_high,
        })
    except Exception as e:
        logger.warning("[react_tool/get_seasonal_context] %s: %s", store_id, e)
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
    Returns: avg_same_dow, today_vs_avg_pct, trend_direction,
             best_same_dow, worst_same_dow, n_samples.
    """
    conn = await _conn()
    try:
        today = date.today()
        dow   = today.weekday()
        now_h = datetime.now().hour

        # Same weekday last 4 occurrences
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

        # Today CA up to current hour
        today_ca = await conn.fetchval("""
            SELECT COALESCE(SUM(lig_ttc), 0)
            FROM (
                SELECT lig_ttc FROM sales.transactions    WHERE store_id=$1 AND date_only=$2 AND heure<=$3
                UNION ALL
                SELECT lig_ttc FROM sales.transactions_rt WHERE store_id=$1 AND date_only=$2 AND heure<=$3
            ) t
        """, store_id, today, now_h)
        today_ca = float(today_ca or 0)

        if values:
            avg_same_dow = sum(values) / len(values)
            vs_avg_pct   = round((today_ca - avg_same_dow) / max(avg_same_dow, 1) * 100, 1)
            trend        = "ABOVE_AVERAGE" if vs_avg_pct > 5 else ("BELOW_AVERAGE" if vs_avg_pct < -5 else "ON_TRACK")
        else:
            avg_same_dow = 0
            vs_avg_pct   = 0
            trend        = "NO_HISTORY"

        return _j({
            "today_ca_so_far":    round(today_ca, 2),
            "avg_same_dow_ca":    round(avg_same_dow, 2),
            "vs_avg_pct":         vs_avg_pct,
            "trend_vs_history":   trend,
            "n_samples":          len(values),
            "current_hour":       now_h,
            "day_name":           ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"][dow],
        })
    except Exception as e:
        logger.warning("[react_tool/get_historical_comparison] %s: %s", store_id, e)
        return _j({"error": str(e)})
    finally:
        await conn.close()


# Liste exportée
REACT_ANALYST_TOOLS = [
    fetch_live_pos,
    compute_eod_forecast,
    compute_realtime_gap,
    get_intraday_trend,
    get_seasonal_context,
    get_historical_comparison,
]
