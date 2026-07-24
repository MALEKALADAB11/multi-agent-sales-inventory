"""
tools.py — Mémoire de l'Agent Analyste.

Ne contient plus que la persistance et la comparaison inter-cycles. Les calculs
métier (gap, urgence, agrégats horaires) vivaient ici en double de ts_engine ;
ils ont été supprimés avec les nodes qui les appelaient. Source de vérité
unique : ts_engine.analyze_store().
"""

import json
import logging
from datetime import datetime

import asyncpg

from app.core.db import acquire_conn, release_conn
from app.sales.core.config import get_config
from app.sales.data.postgres_provider import normalize_store_id

logger = logging.getLogger(__name__)
config = get_config()


async def get_pg_connection() -> asyncpg.Connection:
    """Connexion du pool partage. A rendre via release_conn() dans un finally."""
    return await acquire_conn(connect_timeout=10)


# La table agent_memory est créée par les migrations Alembic
# (db/migrations, baseline 0001). Plus aucun DDL au runtime.


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
        await release_conn(conn)


async def save_analyst_memory(
    store_id: str,
    cycle_id: str,
    memory_data: dict,
    memory_type: str = "cycle_summary",
) -> bool:
    store_id = normalize_store_id(store_id)

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
        await release_conn(conn)


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
        "forecast_mape": state.get("forecast_mape", 0),
        "forecast_engine": (state.get("ts_analysis") or {}).get("model_engine", ""),
        "trend_signal": state.get("trend_signal", "UNKNOWN"),
        "feasibility": state.get("feasibility", "UNKNOWN"),
        "nb_hourly_gaps": len(state.get("hourly_gaps") or []),
        "avg_ticket": features.get("avg_ticket", 0),
        "nb_transactions": features.get("nb_transactions", 0),
        "top_categories": features.get("top_categories", []),
        "top_products": features.get("top_products", []),
        "summary": state.get("analyst_summary", ""),
    }
