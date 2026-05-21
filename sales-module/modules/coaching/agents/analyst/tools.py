"""
tools.py — Outils Agent Analyste.
PostgreSQL + Redis optionnel + Analyst Memory + calculs métier.
"""

import json
import logging
from datetime import datetime
from typing import Optional

import asyncpg

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

from core.config import get_config
from data.postgres_provider import DB_CONFIG, normalize_store_id

logger = logging.getLogger(__name__)
config = get_config()


async def get_pg_connection() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        timeout=10,
        command_timeout=30,
    )


async def ensure_agent_memory_table() -> None:
    conn = await get_pg_connection()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                id SERIAL PRIMARY KEY,
                agent_name VARCHAR(50) NOT NULL,
                store_id VARCHAR(50) NOT NULL,
                cycle_id VARCHAR(100),
                memory_type VARCHAR(50) NOT NULL,
                memory_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_agent_memory_agent_store
            ON agent_memory(agent_name, store_id, created_at DESC);
        """)
        logger.info("[ANALYST MEMORY] Table agent_memory prête ✅")
    finally:
        await conn.close()


def _safe_json_memory(raw_memory) -> dict:
    if raw_memory is None:
        return {}

    if isinstance(raw_memory, dict):
        return raw_memory

    if isinstance(raw_memory, str):
        try:
            return json.loads(raw_memory)
        except Exception:
            return {}

    try:
        return dict(raw_memory)
    except Exception:
        return {}


async def load_analyst_memory(store_id: str, limit: int = 5) -> dict:
    store_id = normalize_store_id(store_id)
    await ensure_agent_memory_table()

    conn = await get_pg_connection()
    try:
        rows = await conn.fetch("""
            SELECT cycle_id, memory_type, memory_data, created_at
            FROM agent_memory
            WHERE agent_name = 'analyst'
              AND store_id = $1
            ORDER BY created_at DESC
            LIMIT $2
        """, store_id, limit)

        memories = []

        for r in rows:
            memory_data = _safe_json_memory(r["memory_data"])

            memories.append({
                "cycle_id": r["cycle_id"],
                "memory_type": r["memory_type"],
                "memory_data": memory_data,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })

        latest = memories[0]["memory_data"] if memories else {}

        return {
            "store_id": store_id,
            "count": len(memories),
            "latest": latest,
            "history": memories,
        }

    except Exception as e:
        logger.warning(f"[ANALYST MEMORY] load failed: {e}")
        return {
            "store_id": store_id,
            "count": 0,
            "latest": {},
            "history": [],
        }

    finally:
        await conn.close()


async def save_analyst_memory(
    store_id: str,
    cycle_id: str,
    memory_data: dict,
    memory_type: str = "cycle_summary",
) -> bool:
    store_id = normalize_store_id(store_id)
    await ensure_agent_memory_table()

    conn = await get_pg_connection()
    try:
        await conn.execute("""
            INSERT INTO agent_memory (
                agent_name,
                store_id,
                cycle_id,
                memory_type,
                memory_data
            )
            VALUES ('analyst', $1, $2, $3, $4::jsonb)
        """, store_id, cycle_id, memory_type, json.dumps(memory_data, default=str))

        logger.info(f"[ANALYST MEMORY] saved cycle={cycle_id} store={store_id}")
        return True

    except Exception as e:
        logger.warning(f"[ANALYST MEMORY] save failed: {e}")
        return False

    finally:
        await conn.close()


def compare_with_memory(current: dict, memory: dict) -> dict:
    latest = memory.get("latest") or {}

    previous_gap = latest.get("gap_pct")
    previous_urgency = latest.get("urgency_level")
    previous_revenue = latest.get("current_revenue")
    previous_avg_ticket = latest.get("avg_ticket")

    current_gap = current.get("gap_pct")
    current_urgency = current.get("urgency_level")
    current_revenue = current.get("current_revenue")
    current_avg_ticket = current.get("avg_ticket")

    gap_trend = "unknown"
    if previous_gap is not None and current_gap is not None:
        if current_gap < previous_gap:
            gap_trend = "improving"
        elif current_gap > previous_gap:
            gap_trend = "worsening"
        else:
            gap_trend = "stable"

    revenue_trend = "unknown"
    if previous_revenue is not None and current_revenue is not None:
        if current_revenue > previous_revenue:
            revenue_trend = "up"
        elif current_revenue < previous_revenue:
            revenue_trend = "down"
        else:
            revenue_trend = "stable"

    ticket_trend = "unknown"
    if previous_avg_ticket is not None and current_avg_ticket is not None:
        if current_avg_ticket > previous_avg_ticket:
            ticket_trend = "up"
        elif current_avg_ticket < previous_avg_ticket:
            ticket_trend = "down"
        else:
            ticket_trend = "stable"

    return {
        "previous_gap": previous_gap,
        "current_gap": current_gap,
        "gap_trend": gap_trend,
        "previous_urgency": previous_urgency,
        "current_urgency": current_urgency,
        "previous_revenue": previous_revenue,
        "current_revenue": current_revenue,
        "revenue_trend": revenue_trend,
        "previous_avg_ticket": previous_avg_ticket,
        "current_avg_ticket": current_avg_ticket,
        "ticket_trend": ticket_trend,
    }


async def get_redis_client():
    if aioredis is None:
        return None
    return aioredis.from_url(config.redis_url, decode_responses=True)


async def fetch_pos_context_redis(store_id: str) -> Optional[dict]:
    store_id = normalize_store_id(store_id)

    try:
        redis = await get_redis_client()
        if redis is None:
            return None

        raw = await redis.get(f"pos:context:{store_id}")
        return json.loads(raw) if raw else None

    except Exception as e:
        logger.warning(f"[REDIS] fetch failed store={store_id}: {e}")
        return None


async def cache_pos_context_redis(store_id: str, context: dict) -> None:
    store_id = normalize_store_id(store_id)

    try:
        redis = await get_redis_client()
        if redis is None:
            return

        await redis.setex(
            f"pos:context:{store_id}",
            config.redis_context_ttl,
            json.dumps(context, default=str),
        )

    except Exception as e:
        logger.warning(f"[REDIS] cache failed store={store_id}: {e}")


def summarize_pos_history(history: list[dict]) -> dict:
    if not history:
        return {
            "total_transactions": 0,
            "total_revenue": 0.0,
            "revenue_last_hour": 0.0,
            "revenue_last_2h": 0.0,
            "avg_transaction_value": 0.0,
            "hourly_trend": [],
        }

    total_revenue = sum(float(r.get("revenue", 0) or 0) for r in history)
    total_tx = len(history)

    revenue_last_hour = sum(
        float(r.get("revenue", 0) or 0)
        for r in history
        if int(r.get("minutes_ago", 999) or 999) <= 60
    )

    revenue_last_2h = sum(
        float(r.get("revenue", 0) or 0)
        for r in history
        if int(r.get("minutes_ago", 999) or 999) <= 120
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
    hourly: dict[int, float] = {}

    for row in history:
        h = row.get("hour")

        if h is None:
            tx_time = row.get("transaction_time")
            if tx_time and hasattr(tx_time, "hour"):
                h = tx_time.hour

        if h is not None:
            hourly[int(h)] = hourly.get(int(h), 0.0) + float(row.get("revenue", 0) or 0)

    return [
        {"hour": h, "revenue": round(rev, 2)}
        for h, rev in sorted(hourly.items())
    ]


def compute_gap_metrics(
    current_revenue: float,
    daily_target: float,
    forecast_end_of_day: float,
) -> dict:
    gap_amount = max(0.0, daily_target - current_revenue)
    gap_percentage = (gap_amount / daily_target * 100) if daily_target > 0 else 0.0

    remaining_gap = max(0.0, daily_target - forecast_end_of_day)
    forecast_covers_gap = forecast_end_of_day >= daily_target

    if gap_amount > 0:
        coverage_pct = min(
            100.0,
            max(0.0, ((forecast_end_of_day - current_revenue) / gap_amount) * 100),
        )
    else:
        coverage_pct = 100.0

    return {
        "gap_amount": round(gap_amount, 2),
        "gap_percentage": round(gap_percentage, 2),
        "forecast_covers_gap": forecast_covers_gap,
        "remaining_gap_after_forecast": round(remaining_gap, 2),
        "forecast_gap_coverage_pct": round(coverage_pct, 2),
    }


def compute_urgency(
    gap_pct: float,
    coverage_pct: float,
    current_hour: int,
    hours_remaining: float,
    avg_ticket: float = 0.0,
    nb_transactions: int = 0,
) -> tuple[str, float]:
    time_pressure = min(1.0, max(0.0, (current_hour - 8) / 12))
    gap_score = min(1.0, gap_pct / 60.0)
    coverage_penalty = max(0.0, (100 - coverage_pct) / 100) * 0.25

    activity_risk = 0.0
    if current_hour >= 10 and nb_transactions <= 2:
        activity_risk = 0.15
    elif current_hour >= 12 and nb_transactions <= 5:
        activity_risk = 0.10

    ticket_risk = 0.0
    if avg_ticket > 0 and avg_ticket < 30 and gap_pct > 10:
        ticket_risk = 0.10

    urgency_score = min(
        1.0,
        gap_score * 0.40
        + time_pressure * 0.25
        + coverage_penalty
        + activity_risk
        + ticket_risk,
    )

    if gap_pct > 45 and coverage_pct < 70:
        level = "CRITICAL"
    elif gap_pct > 30 and coverage_pct < 85:
        level = "HIGH"
    elif gap_pct > 15 or hours_remaining < 3:
        level = "MEDIUM"
    else:
        level = "LOW"

    if hours_remaining < 2 and gap_pct > 10:
        level = "HIGH" if level != "CRITICAL" else "CRITICAL"
        urgency_score = max(urgency_score, 0.85)

    return level, round(urgency_score, 3)


def build_analyst_memory_payload(state: dict) -> dict:
    pos_data = state.get("pos_data") or {}
    features = state.get("analysis_features") or {}

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "business_date": pos_data.get("business_date"),
        "current_revenue": pos_data.get("current_revenue", 0),
        "daily_target": pos_data.get("daily_target", 0),
        "gap_pct": state.get("gap_objectif", 0),
        "gap_amount": state.get("gap_amount", 0),
        "forecast_eod": state.get("forecast_eod", 0),
        "coverage": state.get("coverage", 0),
        "urgency_level": state.get("urgency_level", "LOW"),
        "urgency_score": state.get("urgency_score", 0),
        "avg_ticket": features.get("avg_ticket", 0),
        "nb_transactions": features.get("nb_transactions", 0),
        "top_categories": features.get("top_categories", []),
        "top_products": features.get("top_products", []),
        "summary": state.get("analyst_summary", ""),
    }