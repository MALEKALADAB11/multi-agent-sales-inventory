"""
HITL (Human-In-The-Loop) Review Router
=======================================
Endpoints used by the manager UI to view and validate AI-generated strategies
that require human approval before being acted upon.

Triggers:
  - StrategistAgent: critique_passed=False AND urgency in (CRITICAL, HIGH) AND gap > 40%
  - InventoryDecisionAgent: escalate_to_human=True in decision output

Table: public.hitl_reviews
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hitl", tags=["hitl"])

# ── DB config ────────────────────────────────────────────────────────────────

_DB_URL = os.getenv("DATABASE_URL") or (
    f"postgresql://{os.getenv('PGUSER','ooredoo')}"
    f":{os.getenv('PGPASSWORD','secret')}"
    f"@{os.getenv('PGHOST','localhost')}"
    f":{os.getenv('PGPORT','5432')}"
    f"/{os.getenv('PGDATABASE','ooredoo_sales')}"
)

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=4)
    return _pool


# ── Table bootstrap (called at startup) ─────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.hitl_reviews (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id        TEXT        NOT NULL,
    cycle_id        TEXT        NOT NULL,
    urgency_level   TEXT        NOT NULL,
    gap_pct         FLOAT       NOT NULL,
    critique_score  FLOAT       NOT NULL,
    critique_feedback TEXT      NOT NULL,
    strategie_summary TEXT      NOT NULL,
    actions         JSONB       NOT NULL DEFAULT '[]',
    source          TEXT        NOT NULL DEFAULT 'sales',  -- 'sales' | 'inventory'
    status          TEXT        NOT NULL DEFAULT 'pending', -- 'pending' | 'approved' | 'rejected'
    approver_name   TEXT,
    approver_note   TEXT,
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hitl_reviews_pending
    ON public.hitl_reviews (store_id, status, created_at DESC)
    WHERE status = 'pending';
"""


async def setup_hitl_table() -> None:
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        logger.info("[HITL] Table public.hitl_reviews prête")
    except Exception as e:
        logger.warning("[HITL] setup_hitl_table: %s", e)


# ── Public helper called from StrategistAgent ────────────────────────────────

async def submit_hitl_review(
    *,
    store_id: str,
    cycle_id: str,
    urgency_level: str,
    gap_pct: float,
    critique_score: float,
    critique_feedback: str,
    strategie_summary: str,
    actions: list,
    source: str = "sales",
) -> Optional[str]:
    """
    Insert a pending HITL review row.  Returns the UUID or None on error.
    Called fire-and-forget from node_self_critique_stratege.
    """
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.hitl_reviews
                    (store_id, cycle_id, urgency_level, gap_pct,
                     critique_score, critique_feedback,
                     strategie_summary, actions, source)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
                RETURNING id::text
                """,
                store_id, cycle_id, urgency_level, gap_pct,
                critique_score, critique_feedback,
                strategie_summary,
                __import__("json").dumps(actions),
                source,
            )
        review_id = row["id"]
        logger.info("[HITL] Review soumis %s (store=%s urgency=%s gap=%.1f%%)",
                    review_id, store_id, urgency_level, gap_pct)
        return review_id
    except Exception as e:
        logger.warning("[HITL] submit_hitl_review échoué: %s", e)
        return None


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class ValidatePayload(BaseModel):
    decision: str           # "approved" | "rejected"
    approver_name: str
    approver_note: Optional[str] = None


# ── GET /api/v1/hitl/pending ─────────────────────────────────────────────────

@router.get("/pending")
async def get_pending_reviews(store_id: Optional[str] = None, limit: int = 20):
    """
    List pending HITL reviews for the manager dashboard.
    Optionally filter by store_id.
    """
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            if store_id:
                rows = await conn.fetch(
                    """
                    SELECT id::text, store_id, cycle_id, urgency_level, gap_pct,
                           critique_score, critique_feedback,
                           strategie_summary, actions, source, created_at
                    FROM public.hitl_reviews
                    WHERE status = 'pending' AND store_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    store_id, limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id::text, store_id, cycle_id, urgency_level, gap_pct,
                           critique_score, critique_feedback,
                           strategie_summary, actions, source, created_at
                    FROM public.hitl_reviews
                    WHERE status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return {
            "count": len(rows),
            "reviews": [dict(r) for r in rows],
        }
    except Exception as e:
        logger.error("[HITL] get_pending_reviews: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /api/v1/hitl/validate/{review_id} ──────────────────────────────────

@router.post("/validate/{review_id}")
async def validate_review(review_id: str, body: ValidatePayload):
    """
    Manager approves or rejects a pending strategy.
    decision must be 'approved' or 'rejected'.
    """
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")

    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE public.hitl_reviews
                SET status        = $1,
                    approver_name = $2,
                    approver_note = $3,
                    reviewed_at   = NOW()
                WHERE id = $4::uuid AND status = 'pending'
                """,
                body.decision, body.approver_name, body.approver_note,
                review_id,
            )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404,
                                detail="Review introuvable ou déjà traitée")
        logger.info("[HITL] Review %s → %s par %s",
                    review_id, body.decision, body.approver_name)
        return {"id": review_id, "status": body.decision, "approver": body.approver_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[HITL] validate_review: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/v1/hitl/stats ───────────────────────────────────────────────────

@router.get("/stats")
async def get_hitl_stats(store_id: Optional[str] = None):
    """Counts by status for the dashboard badge."""
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            if store_id:
                rows = await conn.fetch(
                    """
                    SELECT status, COUNT(*) AS cnt
                    FROM public.hitl_reviews
                    WHERE store_id = $1
                      AND created_at >= NOW() - INTERVAL '24 hours'
                    GROUP BY status
                    """,
                    store_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT status, COUNT(*) AS cnt
                    FROM public.hitl_reviews
                    WHERE created_at >= NOW() - INTERVAL '24 hours'
                    GROUP BY status
                    """
                )
        stats = {r["status"]: int(r["cnt"]) for r in rows}
        return {
            "pending":  stats.get("pending", 0),
            "approved": stats.get("approved", 0),
            "rejected": stats.get("rejected", 0),
        }
    except Exception as e:
        logger.error("[HITL] get_hitl_stats: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
