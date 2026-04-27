"""
Outils de l'Agent Analyste : BigQuery, TimesFM, Redis, calculs.
"""
import asyncio
import json
import logging
from datetime import datetime, date
from typing import Optional

import httpx
import redis.asyncio as aioredis
from google.cloud import bigquery

from sales_module.core.config import get_config

logger = logging.getLogger(__name__)
config = get_config()


# ─────────────────────────────────────────────
# Redis — Contexte < 10ms
# ─────────────────────────────────────────────

async def get_redis_client() -> aioredis.Redis:
    return aioredis.from_url(config.redis_url, decode_responses=True)


async def fetch_pos_context_redis(store_id: str) -> Optional[dict]:
    """Récupère le contexte POS enrichi depuis Redis (< 10ms)."""
    try:
        redis = await get_redis_client()
        raw = await redis.get(f"pos:context:{store_id}")
        if raw:
            return json.loads(raw)
        return None
    except Exception as e:
        logger.warning(f"Redis fetch failed for store {store_id}: {e}")
        return None


async def cache_pos_context_redis(store_id: str, context: dict) -> None:
    """Met en cache le contexte POS enrichi."""
    try:
        redis = await get_redis_client()
        await redis.setex(
            f"pos:context:{store_id}",
            config.redis_context_ttl,
            json.dumps(context)
        )
    except Exception as e:
        logger.warning(f"Redis cache failed for store {store_id}: {e}")


# ─────────────────────────────────────────────
# BigQuery — Historique POS
# ─────────────────────────────────────────────

async def fetch_pos_history_bigquery(store_id: str, today: Optional[date] = None) -> list[dict]:
    """
    Récupère l'historique POS du jour depuis BigQuery.
    Retourne les transactions horodatées pour analyse de tendance.
    """
    if today is None:
        today = date.today()

    query = f"""
        SELECT
            transaction_id,
            transaction_time,
            revenue,
            product_category,
            seller_id,
            TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), transaction_time, MINUTE) as minutes_ago
        FROM `{config.bigquery_project}.{config.bigquery_dataset}.{config.bigquery_pos_table}`
        WHERE
            store_id = @store_id
            AND DATE(transaction_time) = @today
        ORDER BY transaction_time ASC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("store_id", "STRING", store_id),
            bigquery.ScalarQueryParameter("today", "DATE", today.isoformat()),
        ]
    )

    try:
        client = bigquery.Client(project=config.bigquery_project)
        # Run in thread to avoid blocking event loop
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None,
            lambda: list(client.query(query, job_config=job_config).result())
        )
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"BigQuery fetch failed: {e}")
        return []


def summarize_pos_history(history: list[dict]) -> dict:
    """
    Calcule les métriques agrégées depuis l'historique POS.
    """
    if not history:
        return {
            "total_transactions": 0,
            "total_revenue": 0.0,
            "revenue_last_hour": 0.0,
            "revenue_last_2h": 0.0,
            "avg_transaction_value": 0.0,
            "hourly_trend": [],
        }

    total_revenue = sum(r.get("revenue", 0) for r in history)
    total_tx = len(history)

    revenue_last_hour = sum(
        r.get("revenue", 0) for r in history
        if r.get("minutes_ago", 999) <= 60
    )
    revenue_last_2h = sum(
        r.get("revenue", 0) for r in history
        if r.get("minutes_ago", 999) <= 120
    )

    return {
        "total_transactions": total_tx,
        "total_revenue": total_revenue,
        "revenue_last_hour": revenue_last_hour,
        "revenue_last_2h": revenue_last_2h,
        "avg_transaction_value": total_revenue / total_tx if total_tx > 0 else 0.0,
        "hourly_trend": _compute_hourly_trend(history),
    }


def _compute_hourly_trend(history: list[dict]) -> list[dict]:
    """Agrège les revenus par heure pour détection de tendance."""
    hourly: dict[int, float] = {}
    for row in history:
        tx_time = row.get("transaction_time")
        if tx_time and hasattr(tx_time, "hour"):
            h = tx_time.hour
            hourly[h] = hourly.get(h, 0.0) + row.get("revenue", 0.0)
    return [{"hour": h, "revenue": rev} for h, rev in sorted(hourly.items())]


# ─────────────────────────────────────────────
# TimesFM — Prévision fin de journée
# ─────────────────────────────────────────────

async def call_timesfm_forecast(
    store_id: str,
    history_values: list[float],
    current_hour: int,
) -> dict:
    """
    Appelle le service TimesFM pour obtenir la prévision de CA fin de journée.
    
    Returns:
        {
            "forecast_end_of_day": float,
            "confidence_interval": {"low": float, "high": float},
            "forecast_horizon_hours": int,
            "model_version": str
        }
    """
    payload = {
        "store_id": store_id,
        "context_values": history_values,
        "horizon": config.timesfm_horizon_hours - (current_hour % config.timesfm_horizon_hours),
        "frequency": "H",  # Hourly
        "context_length": config.timesfm_context_length,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                config.timesfm_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

            # Somme des prévisions horaires restantes
            forecast_values = data.get("forecast", [])
            forecast_total = sum(forecast_values)
            current_revenue = sum(history_values)

            return {
                "forecast_end_of_day": current_revenue + forecast_total,
                "forecast_remaining": forecast_total,
                "forecast_hourly": forecast_values,
                "confidence_interval": data.get("confidence_interval", {}),
                "forecast_horizon_hours": len(forecast_values),
                "model_version": data.get("model_version", "timesfm-1.0"),
            }

    except httpx.TimeoutException:
        logger.warning(f"TimesFM timeout for store {store_id}, using fallback")
        return _timesfm_fallback(history_values)
    except Exception as e:
        logger.error(f"TimesFM error: {e}")
        return _timesvm_fallback(history_values)


def _timesvm_fallback(history_values: list[float]) -> dict:
    """Fallback linéaire si TimesFM indisponible."""
    if not history_values or len(history_values) < 2:
        return {"forecast_end_of_day": 0.0, "forecast_remaining": 0.0, "source": "fallback"}

    # Extrapolation linéaire simple
    recent_avg = sum(history_values[-3:]) / min(3, len(history_values))
    current_hour = datetime.now().hour
    remaining_hours = max(0, 20 - current_hour)  # Fermeture à 20h
    current_revenue = sum(history_values)

    return {
        "forecast_end_of_day": current_revenue + (recent_avg * remaining_hours),
        "forecast_remaining": recent_avg * remaining_hours,
        "forecast_hourly": [recent_avg] * remaining_hours,
        "confidence_interval": {},
        "forecast_horizon_hours": remaining_hours,
        "source": "linear_fallback",
    }

_timesfm_fallback = _timesvm_fallback  # alias


# ─────────────────────────────────────────────
# Calculs Gap & Urgence
# ─────────────────────────────────────────────

def compute_gap_metrics(
    current_revenue: float,
    daily_target: float,
    forecast_end_of_day: float,
) -> dict:
    """
    Calcule le gap et les métriques d'urgence.
    """
    gap_amount = daily_target - current_revenue
    gap_percentage = (gap_amount / daily_target * 100) if daily_target > 0 else 0.0
    gap_percentage = max(0.0, gap_percentage)

    remaining_gap = daily_target - forecast_end_of_day
    forecast_covers_gap = forecast_end_of_day >= daily_target

    # Couverture du gap par la prévision (0% = pas de couverture, 100% = objectif atteint)
    if gap_amount > 0:
        coverage_pct = min(100.0, ((forecast_end_of_day - current_revenue) / gap_amount) * 100)
    else:
        coverage_pct = 100.0

    return {
        "gap_amount": gap_amount,
        "gap_percentage": gap_percentage,
        "forecast_covers_gap": forecast_covers_gap,
        "remaining_gap_after_forecast": max(0.0, remaining_gap),
        "forecast_gap_coverage_pct": coverage_pct,
    }


def compute_urgency(
    gap_pct: float,
    coverage_pct: float,
    current_hour: int,
    hours_remaining: float,
) -> tuple[str, float]:
    """
    Détermine le niveau d'urgence et le score numérique.
    
    Returns:
        (urgency_level: str, urgency_score: float 0-1)
    """
    # Facteur temps : plus l'heure est tardive, plus l'urgence augmente
    time_pressure = min(1.0, (current_hour - 8) / 10)  # Normalise 8h-18h

    # Score brut basé sur le gap
    gap_score = min(1.0, gap_pct / 50.0)

    # Pénalité si la prévision ne couvre pas le gap
    coverage_penalty = max(0.0, (100 - coverage_pct) / 100) * 0.3

    urgency_score = min(1.0, (gap_score * 0.5) + (time_pressure * 0.3) + coverage_penalty)

    # Classification
    if gap_pct > config.urgency_high_threshold * 100 and coverage_pct < 80:
        level = "HIGH"
    elif gap_pct > config.urgency_medium_threshold * 100:
        level = "MEDIUM"
    else:
        level = "LOW"

    # Override : si < 2h restantes et gap significatif → HIGH
    if hours_remaining < 2 and gap_pct > 10:
        level = "HIGH"
        urgency_score = max(urgency_score, 0.85)

    return level, round(urgency_score, 3)