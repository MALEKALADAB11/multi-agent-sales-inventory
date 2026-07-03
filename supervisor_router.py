"""
supervisor_router.py — HTTP entry-point for the SupervisorAgent LangGraph (S8.1)

Endpoints:
  POST /api/v1/supervisor/run   — Trigger a full cycle synchronously and return result
  POST /api/v1/supervisor/async — Fire-and-forget (results are pushed via WS)
  GET  /api/v1/supervisor/status/{store_id} — Last run status
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/supervisor", tags=["supervisor"])

# ── Per-store last-run cache ──────────────────────────────────────────────────
_last_run: Dict[str, Dict[str, Any]] = {}


class SupervisorRunRequest(BaseModel):
    store_id:     str   = "store-lac2"
    advisor_id:   Optional[str] = None
    trigger_type: str   = "manual"
    user_message: Optional[str] = None


# ── Store ID normalizer (mirrors main.py STORE_MAP) ──────────────────────────
_STORE_MAP = {
    "store-lac2":   "I63", "lac2":  "I63", "OOR_LAC_01":  "I63",
    "store-menzah": "M01", "menzah":"M01", "OOR_MENZAH_02":"M01",
    "store-sfax":   "S01", "sfax":  "S01", "OOR_SFAX_03": "S01",
}

def _normalize(store_id: str) -> str:
    return _STORE_MAP.get(store_id, store_id)


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/supervisor/run  — synchronous full-cycle
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/run")
async def supervisor_run(body: SupervisorRunRequest):
    """
    Run the full SupervisorAgent LangGraph cycle for a store.
    Returns the final RetailState as JSON (coach_recommendation, guardrail_status,
    scored_products, inventory_decisions, ...).
    Blocks until the graph completes (< 10s typical).
    """
    mapped_id = _normalize(body.store_id)
    cycle_id  = f"sup-{uuid.uuid4().hex[:8]}"
    t0        = time.time()

    try:
        from orchestration.supervisor_agent import get_supervisor_graph
        graph = get_supervisor_graph()
    except Exception as e:
        logger.error("[Supervisor] Graph load failed: %s", e)
        raise HTTPException(status_code=503, detail=f"SupervisorAgent unavailable: {e}")

    # Build initial RetailState
    initial_state: Dict[str, Any] = {
        "cycle_id":     cycle_id,
        "store_id":     mapped_id,
        "advisor_id":   body.advisor_id,
        "trigger_type": body.trigger_type,
        "user_message": body.user_message,
        "agents_invoked": [],
        "errors":         [],
        "metrics":        {},
    }

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=150.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Supervisor cycle timed out (150s)")
    except Exception as e:
        logger.error("[Supervisor] Cycle error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    latency_ms = round((time.time() - t0) * 1000)

    # Cache last result per store
    summary = {
        "cycle_id":           cycle_id,
        "store_id":           mapped_id,
        "latency_ms":         latency_ms,
        "guardrail_status":   result.get("guardrail_status", "APPROVE"),
        "urgency_level":      result.get("urgency_level", ""),
        "coach_recommendation": result.get("coach_recommendation", {}),
        "scored_products":    result.get("scored_products", [])[:3],
        "critical_alerts":    result.get("critical_stock_alerts", [])[:3],
        "agents_invoked":     result.get("agents_invoked", []),
        "errors":             result.get("errors", []),
        "metrics":            result.get("metrics", {}),
        "hitl_required":      result.get("hitl_required", False),
        "timestamp":          __import__("datetime").datetime.utcnow().isoformat(),
    }
    _last_run[mapped_id] = summary

    logger.info(
        "[Supervisor] /run %s — %dms | guardrail=%s | urgency=%s | agents=%s",
        mapped_id, latency_ms,
        summary["guardrail_status"], summary["urgency_level"],
        summary["agents_invoked"],
    )

    return JSONResponse(summary)


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/supervisor/async  — fire and forget, results via WS broadcast
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/async")
async def supervisor_run_async(body: SupervisorRunRequest):
    """
    Trigger the SupervisorAgent in background.
    Results are pushed to the frontend via WebSocket (coach_recommendation event).
    Returns immediately with cycle_id.
    """
    mapped_id = _normalize(body.store_id)
    cycle_id  = f"sup-async-{uuid.uuid4().hex[:8]}"

    async def _bg():
        try:
            from orchestration.supervisor_agent import get_supervisor_graph
            graph = get_supervisor_graph()
            initial: Dict[str, Any] = {
                "cycle_id":     cycle_id,
                "store_id":     mapped_id,
                "advisor_id":   body.advisor_id,
                "trigger_type": body.trigger_type,
                "user_message": body.user_message,
                "agents_invoked": [],
                "errors":         [],
                "metrics":        {},
            }
            result = await asyncio.wait_for(graph.ainvoke(initial), timeout=150.0)
            _last_run[mapped_id] = {
                "cycle_id":         cycle_id,
                "guardrail_status": result.get("guardrail_status", "APPROVE"),
                "urgency_level":    result.get("urgency_level", ""),
                "agents_invoked":   result.get("agents_invoked", []),
                "errors":           result.get("errors", []),
            }
            logger.info("[Supervisor] /async %s done — guardrail=%s",
                        mapped_id, result.get("guardrail_status"))
        except Exception as e:
            logger.error("[Supervisor] /async %s error: %s", mapped_id, e)

    asyncio.create_task(_bg())
    return JSONResponse({"cycle_id": cycle_id, "status": "running", "store_id": mapped_id})


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/supervisor/status/{store_id}
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/status/{store_id}")
async def supervisor_status(store_id: str):
    """Return the last SupervisorAgent run summary for a store."""
    mapped_id = _normalize(store_id)
    cached    = _last_run.get(mapped_id)
    if not cached:
        return JSONResponse({"status": "no_run", "store_id": mapped_id})
    return JSONResponse({**cached, "status": "last_run"})
