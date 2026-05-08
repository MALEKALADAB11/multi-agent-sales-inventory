"""
Inventory API Routes — FIXED REAL-TIME WEBSOCKET BROADCASTING
=============================================================
Fixes applied:
  1. invalidate_store() no longer clears _stock_overrides — sales data must
     survive until analysis reads it.
  2. _push_update_to_store() uses force_refresh=True so it always re-runs
     the pipeline instead of reading the stale cache.
  3. Broadcast is always scheduled, not gated on cache keys existing.
  4. Debounce: rapid sales are coalesced into one analysis run per store.
"""

from __future__ import annotations

import logging
import time
import asyncio
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

# Add these imports
from shared_module.store_mapper import to_canonical_store_id, to_frontend_store_id
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from config.settings import STOCK_HISTORY_PATH, PRODUCT_MASTER_PATH
from src.services.orchestrator import create_orchestrator
from src.tools.internal.stock_tools import _DataCache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inventory"])

# Two orchestrators: full LLM for on-demand HTTP, fast rule-based for live broadcasts
_orchestrator      = None  # use_llm follows .env (for HTTP endpoints)
_orchestrator_fast = None  # use_llm=False always (for WebSocket broadcasts)

# ---------------------------------------------------------------------------
# Result cache
# ---------------------------------------------------------------------------

CACHE_TTL = 1200  # seconds (20 minutes)
_store_cache: Dict[str, Dict[str, Any]] = {}


def _cache_key(store_id: str, objective: str) -> str:
    return f"{store_id}::{objective}"


def _get_cached(store_id: str, objective: str) -> Optional[Dict[str, Any]]:
    entry = _store_cache.get(_cache_key(store_id, objective))
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        logger.info("Cache HIT  %s (age %ds)", _cache_key(store_id, objective),
                    int(time.time() - entry["ts"]))
        return entry["data"]
    return None


def _set_cache(store_id: str, objective: str, data: Dict[str, Any]) -> None:
    _store_cache[_cache_key(store_id, objective)] = {"data": data, "ts": time.time()}
    logger.info("Cache SET  %s", _cache_key(store_id, objective))


# ---------------------------------------------------------------------------
# WebSocket connection registry
# ---------------------------------------------------------------------------

# Maps store_id → list of (websocket, business_objective) tuples
_active_ws_connections: Dict[str, List[tuple[WebSocket, str]]] = {}

# Debounce: track pending broadcast tasks per store to avoid stampede
_pending_broadcasts: Dict[str, asyncio.Task] = {}
BROADCAST_DEBOUNCE_SECONDS = 2.0  # coalesce rapid sales into one analysis run


async def _broadcast_to_store(store_id: str, message: dict) -> None:
    """Push a message to all WebSockets connected to a store."""
    connections = _active_ws_connections.get(store_id, [])
    dead = []
    for ws, _ in connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    # Clean up dead connections
    for ws in dead:
        connections[:] = [(w, obj) for w, obj in connections if w != ws]


async def _broadcast_stock_delta(store_id: str, sku: str, new_stock: float,
                                  risk_level: str, days_of_stock: float,
                                  coverage_ratio: float, risk_rationale: str) -> None:
    """
    Instant lightweight push — fires immediately on every sale (< 100ms).
    Lets the frontend update the quadrant dot, KPI card, and product card
    right away, before the full re-analysis pipeline finishes (~10s).
    """
    message = {
        "type":           "stock_delta",
        "store_id":       store_id,
        "sku":            sku,
        "new_stock":      round(new_stock),
        "risk_level":     risk_level,
        "days_of_stock":  round(days_of_stock, 1),
        "coverage_ratio": round(coverage_ratio, 2),
        "risk_rationale": risk_rationale,
        "timestamp":      time.time(),
    }
    await _broadcast_to_store(store_id, message)
    logger.debug("stock_delta broadcast: %s → stock=%s risk=%s", sku, new_stock, risk_level)


async def _push_update_to_store(store_id: str) -> None:
    """
    Re-run analysis (force_refresh=True) and broadcast to all WebSockets.
    Called after debounce delay so rapid sales are coalesced.
    """
    # Small debounce delay — lets rapid consecutive sales batch together
    await asyncio.sleep(BROADCAST_DEBOUNCE_SECONDS)

    connections = _active_ws_connections.get(store_id, [])
    if not connections:
        logger.info("No WebSocket connections for %s, skipping broadcast", store_id)
        return

    # Group by objective so we run analysis once per objective
    objectives = set(obj for _, obj in connections)

    for business_objective in objectives:
        try:
            loop = asyncio.get_event_loop()
            # FIX: force_refresh=True — always re-run pipeline with updated stock
            payload = await loop.run_in_executor(
                None,
                lambda obj=business_objective: analyze_store(
                    store_id, obj, force_refresh=True, fast=True
                ),
            )

            message = {
                "type": "inventory_update",
                "store_id": store_id,
                "business_objective": business_objective,
                **payload,
            }

            target_connections = [ws for ws, obj in connections if obj == business_objective]
            for ws in target_connections:
                try:
                    await ws.send_json(message)
                except Exception as exc:
                    logger.warning("Failed to send to WebSocket: %s", exc)

            logger.info(
                "Pushed inventory update (%s) to %d WebSocket(s) for %s",
                business_objective, len(target_connections), store_id,
            )

        except Exception as exc:
            logger.error(
                "Failed to push update for %s (%s): %s",
                store_id, business_objective, exc,
            )

    # Clean up the pending task entry
    _pending_broadcasts.pop(store_id, None)


def invalidate_store(store_id: str, sku: str = None, new_stock: float = None) -> None:
    """
    Drop cached results for a store and schedule a WebSocket broadcast.

    Two-phase push:
      Phase 1 (instant, < 100ms): stock_delta message with recomputed risk for
        the changed SKU only. Lets the frontend update immediately.
      Phase 2 (debounced, ~10s): full inventory_update after re-analysis pipeline.

    Called by the sales simulator hook in main.py on every sale.
    """
    # Drop result cache (safe — analysis will re-read from _DataCache)
    keys_to_drop = [k for k in _store_cache if k.startswith(f"{store_id}::")]
    for k in keys_to_drop:
        del _store_cache[k]

    if keys_to_drop:
        logger.info(
            "invalidate_store(%s): dropped %d cache entries",
            store_id, len(keys_to_drop),
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            def _schedule():
                # ── Phase 1: instant stock_delta for the sold SKU ─────────
                if sku is not None and new_stock is not None:
                    # Recompute risk locally without running the full pipeline
                    risk_level, days_of_stock, coverage_ratio, rationale = \
                        _quick_risk(store_id, sku, new_stock)
                    asyncio.create_task(_broadcast_stock_delta(
                        store_id, sku, new_stock,
                        risk_level, days_of_stock, coverage_ratio, rationale,
                    ))

                # ── Phase 2: debounced full re-analysis ───────────────────
                old_task = _pending_broadcasts.get(store_id)
                if old_task and not old_task.done():
                    old_task.cancel()
                task = asyncio.create_task(_push_update_to_store(store_id))
                _pending_broadcasts[store_id] = task

            loop.call_soon_threadsafe(_schedule)
    except RuntimeError:
        logger.warning("No event loop available for WebSocket broadcast")


def _quick_risk(store_id: str, sku: str, current_stock: float):
    """
    Fast, in-process risk recomputation for a single SKU.
    No LLM, no full pipeline — just the threshold math from stock_tools.
    Returns (risk_level, days_of_stock, coverage_ratio, rationale).
    """
    try:
        prod_df = _DataCache.product()
        rows = prod_df[prod_df["sku"] == sku]
        if rows.empty:
            return "ok", 999.0, 5.0, ""
        p = rows.iloc[0]

        lead_time_avg = float(p.get("lead_time_days", 7))
        lead_time_std = float(p.get("lead_time_std", 0))

        forecast_df = _DataCache.forecast()
        if "sku" in forecast_df.columns:
            forecast_df = forecast_df[forecast_df["sku"] == sku]
        if "store_id" in forecast_df.columns:
            forecast_df = forecast_df[forecast_df["store_id"] == store_id]

        avg_daily = float(forecast_df["predicted_demand"].mean()) if not forecast_df.empty else 1.0
        if avg_daily <= 0:
            avg_daily = 1.0

        days_remaining = current_stock / avg_daily
        coverage_ratio = round(days_remaining / lead_time_avg, 2) if lead_time_avg else 0
        lt_var = lead_time_std * 2

        if days_remaining < lead_time_avg:
            risk_level = "critical"
            rationale = (
                f"Stock ({days_remaining:.1f}d) will run out before next order "
                f"(lead time {lead_time_avg:.0f}d). Stockout imminent."
            )
        elif days_remaining < lead_time_avg + lt_var:
            risk_level = "high"
            rationale = (
                f"Stock ({days_remaining:.1f}d) within lead time variability window "
                f"({lead_time_avg:.0f}d ± {lead_time_std:.1f}d). Delayed shipment = stockout."
            )
        elif days_remaining < lead_time_avg * 2.5:
            risk_level = "medium"
            rationale = f"Stock ({days_remaining:.1f}d) below 2.5× lead time. Monitor."
        else:
            risk_level = "ok"
            rationale = f"Stock ({days_remaining:.1f}d) exceeds 2.5× lead time. Well covered."

        return risk_level, round(days_remaining, 1), coverage_ratio, rationale

    except Exception as exc:
        logger.warning("_quick_risk failed for %s: %s", sku, exc)
        return "ok", 0.0, 0.0, ""


def get_orchestrator():
    """Full orchestrator — uses LLM when enabled. For HTTP endpoints."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = create_orchestrator()
    return _orchestrator


def get_orchestrator_fast():
    """Fast orchestrator — LLM always off. For WebSocket live broadcasts."""
    global _orchestrator_fast
    if _orchestrator_fast is None:
        _orchestrator_fast = create_orchestrator(use_llm=False)
    return _orchestrator_fast


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    sku: str
    store_id: str = Field(default="STORE-001")
    business_objective: str = Field(default="balanced")


class BatchAnalyzeRequest(BaseModel):
    store_id: str = Field(default="STORE-001")
    business_objective: str = Field(default="balanced")
    skus: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Product master enrichment
# ---------------------------------------------------------------------------

def _enrich_with_product_master(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        pm = (
            _DataCache.product()
            .set_index("sku")
            [["product_name", "category", "unit_cost", "moq", "lead_time_days"]]
            .rename(columns={"product_name": "name"})
        )
    except Exception as exc:
        logger.warning("Could not load product_master for enrichment: %s", exc)
        return results

    enriched = []
    for r in results:
        sku = r.get("sku", "")
        if sku in pm.index:
            r = dict(r)
            r["product_info"] = pm.loc[sku].to_dict()
        enriched.append(r)
    return enriched


# ---------------------------------------------------------------------------
# Shape adapter: orchestrator result → Angular InventoryItem
# ---------------------------------------------------------------------------

RISK_MAP = {
    "CRITICAL": "critical",
    "HIGH":     "high",
    "MEDIUM":   "medium",
    "LOW":      "ok",
}

RISK_SCORE_MAP = {
    "critical": 0.90,
    "high":     0.72,
    "medium":   0.45,
    "ok":       0.10,
}


def _to_inventory_item(result: Dict[str, Any]) -> Dict[str, Any]:
    if "error" in result:
        return {"sku": result.get("sku", ""), "error": result["error"]}

    report   = result.get("analysis_report", {})
    stock    = report.get("stock_status", {})
    forecast = report.get("forecast", {})
    metrics  = report.get("metrics", {})
    risk     = report.get("risk_assessment", {})
    constr   = report.get("constraints", {})
    pi       = result.get("product_info", {})

    sku      = result["sku"]
    store_id = result.get("store_id", "STORE-001")

    # FIX: Read stock directly from _DataCache — not from the parsed LLM report.
    # When the LLM fails (429 → rule-based fallback), analysis_report is empty
    # or stale and never reflects live sales depletion. _DataCache.get_current_stock()
    # always returns the post-sale override value set by record_sale().
    live_stock    = _DataCache.get_current_stock(sku, store_id)
    report_stock  = stock.get("current_stock", 0)
    current_stock = live_stock if live_stock > 0 else report_stock

    avg_daily  = forecast.get("avg_daily_demand", 1) or 1
    lead_time  = stock.get("lead_time_avg_days", 7)

    # Recompute days_remaining from live stock so risk level reflects reality
    days_remain = current_stock / avg_daily if avg_daily > 0 else 0

    # FIX: Recompute risk level from live days_remain so it reflects real-time
    # stock, not the LLM's stale classification from before the sale was recorded.
    lead_time_std = 0.0
    try:
        prod_rows = _DataCache.product()
        prod_rows = prod_rows[prod_rows["sku"] == sku]
        if not prod_rows.empty:
            lead_time_std = float(prod_rows.iloc[0].get("lead_time_std", 0))
    except Exception:
        pass

    lt_var = lead_time_std * 2
    if days_remain < lead_time:
        risk_level_raw = "CRITICAL"
    elif days_remain < lead_time + lt_var:
        risk_level_raw = "HIGH"
    elif days_remain < lead_time * 2.5:
        risk_level_raw = "MEDIUM"
    else:
        risk_level_raw = "LOW"

    risk_level = RISK_MAP.get(risk_level_raw, "medium")
    risk_score = RISK_SCORE_MAP.get(risk_level, 0.5)

    coverage_ratio = round(days_remain / lead_time, 2) if lead_time else 0

    trend_raw = forecast.get("trend_direction", "stable").lower()
    trend = (
        "up"   if "up"   in trend_raw or "increas" in trend_raw else
        "down" if "down" in trend_raw or "decreas" in trend_raw else
        "stable"
    )

    return {
        "id":               f"inv-{sku}",
        "sku":              sku,
        "name":             pi.get("name", sku),
        "category":         pi.get("category", "Unknown"),
        "stock":            round(current_stock),
        "stockMin":         stock.get("reorder_point", 0),
        "stockMax":         stock.get("reorder_point", 0) * 2,
        "demandForecast24h": round(avg_daily),
        "coverageRatio":    coverage_ratio,
        "riskLevel":        risk_level,
        "riskScore":        risk_score,
        "riskRationale":    risk.get("rationale", ""),
        "trend":            trend,
        "confidence":       0.85,
        "lastUpdated":      result.get("timestamp", ""),
        "daysOfStock":      round(days_remain, 1),
        "leadTimeDays":     lead_time,
        "reorderPoint":     stock.get("reorder_point", 0),
        "safetyStock":      metrics.get("safety_stock", 0),
        # NOTE: key names must match _parse_tool_outputs() in analysis_agent.py exactly
        "safetyStockCostDt": metrics.get("safety_stock_cost_dt", 0),
        "eoq":              metrics.get("eoq", 0),
        "formulaOrderQty":  metrics.get("formula_order_qty", 0),
        "totalReplenishmentCost": metrics.get("total_replenishment_cost", 0),
        "holdingCostPerCycleDt": metrics.get("holding_cost_per_cycle_dt", 0),
        "effectiveServiceLevel": metrics.get("effective_service_level", 0.95),
        "zScore":           metrics.get("z_score", 1.645),
        "moqIsBinding":     constr.get("moq_is_binding", False),
        "moqBindingNote":   constr.get("moq_binding_note", ""),
        "overstockFlag":    risk.get("overstock_flag", False),
        "highCostFlag":     constr.get("high_cost_flag", False),
        "highHoldingFlag":  constr.get("high_holding_flag", False),
        # analystNote: combine objective_note + risk rationale for flip card back
        "analystNote":      " | ".join(filter(None, [
                                report.get("objective_note", ""),
                                risk.get("rationale", ""),
                            ])),
        "unitCost":         pi.get("unit_cost", 0),
        "moq":              pi.get("moq", 0),
        "recommendation":   None,
        "recommendationDetail": None,
        "finalOrderQty":    None,
        "orderTiming":      None,
    }


def _build_alerts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts = []
    for item in items:
        if item.get("error"):
            continue

        risk  = item["riskLevel"]
        stock = item["stock"]
        name  = item["name"]
        days  = item["daysOfStock"]
        lt    = item["leadTimeDays"]
        sku   = item["sku"]

        if risk == "critical" and stock < 10:
            alerts.append({
                "id":      f"alert-rupture-{sku}",
                "type":    "rupture",
                "urgency": "critical",
                "title":   f"Stockout imminent: {name}",
                "message": (
                    f"{name} — only {stock:.0f} units left, "
                    f"{days:.1f} days of stock remaining"
                ),
                "action":  None,
                "time":    item["lastUpdated"],
            })
        elif risk == "high":
            alerts.append({
                "id":      f"alert-high-{sku}",
                "type":    "redistribution",
                "urgency": "high",
                "title":   f"Low stock: {name}",
                "message": (
                    f"{name} — {days:.1f}d of stock within lead-time "
                    f"variability window ({lt:.0f}d avg)"
                ),
                "action":  None,
                "time":    item["lastUpdated"],
            })
    return alerts


def _build_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid    = [i for i in items if not i.get("error")]
    total    = len(valid)
    critical = sum(1 for i in valid if i["riskLevel"] == "critical")
    high     = sum(1 for i in valid if i["riskLevel"] == "high")
    ok       = sum(1 for i in valid if i["riskLevel"] in ("ok", "medium"))

    coverages = [i["coverageRatio"] for i in valid if i["coverageRatio"] < 50]
    avg_cov   = round(sum(coverages) / len(coverages), 2) if coverages else 0

    return {
        "totalSkus":        total,
        "criticalCount":    critical,
        "highCount":        high,
        "okCount":          ok,
        "allOk":            critical == 0 and high == 0,
        "avgCoverageRatio": avg_cov,
        "backLines": [
            f"Critical: {critical} SKU{'s' if critical != 1 else ''}",
            f"High risk: {high} SKU{'s' if high != 1 else ''}",
            f"Avg. coverage ratio: {avg_cov}×",
        ],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/skus")
def list_skus(store_id: Optional[str] = Query(default=None)) -> Dict[str, List[str]]:
    df = _DataCache.stock()
    if store_id:
        df = df[df["store_id"] == store_id]
    return {"skus": sorted(df["sku"].unique().tolist())}


@router.get("/stores")
def list_stores() -> Dict[str, List[str]]:
    df = _DataCache.stock()
    return {"stores": sorted(df["store_id"].unique().tolist())}


@router.post("/analyze")
def analyze_single(req: AnalyzeRequest) -> Dict[str, Any]:
    """Single-SKU on-demand analysis. Not cached."""
    result = get_orchestrator().analyze_sku(
        req.sku, req.store_id, req.business_objective
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    [result] = _enrich_with_product_master([result])
    return {
        "raw":  result,
        "item": _to_inventory_item(result),
    }


@router.get("/store/{store_id}")
def analyze_store(    
    store_id: str,
    business_objective: str = Query(default="balanced"),
    force_refresh: bool = Query(default=False),
    fast: bool = False,  # internal flag — True uses rule-based orchestrator (WebSocket path)
) -> Dict[str, Any]:
    """
    Full store analysis with result caching.
    First call runs the pipeline, later calls served from cache instantly.
    force_refresh=True always re-runs the pipeline (used by WebSocket broadcasts).
    fast=True uses the rule-based orchestrator (no LLM) for live broadcast updates.
    """
    canonical_id = to_canonical_store_id(store_id)
    if not force_refresh:
        cached = _get_cached(canonical_id, business_objective)
        if cached is not None:
            cached["store_id"] = to_frontend_store_id(canonical_id)
            return cached

    df   = _DataCache.stock()
    skus = sorted(df[df["store_id"] == canonical_id]["sku"].unique().tolist())
    if not skus:
        raise HTTPException(status_code=404, detail=f"No SKUs found for store '{store_id}'")

    orchestrator = get_orchestrator_fast() if fast else get_orchestrator()
    logger.info("Running pipeline for %s (%d SKUs) [fast=%s]...", canonical_id, len(skus), fast)
    results = orchestrator.analyze_batch(skus, canonical_id, business_objective)
    results = _enrich_with_product_master(results)

    items   = [_to_inventory_item(r) for r in results]
    alerts  = _build_alerts(items)
    payload = {
        "store_id":            to_frontend_store_id(canonical_id),
        "business_objective": business_objective,
        "items":              items,
        "alerts":             alerts,
        "summary":            _build_summary(items),
    }

    _set_cache(canonical_id, business_objective, payload)
    return payload


@router.get("/summary/{store_id}")
def get_summary(
    store_id: str,
    business_objective: str = Query(default="balanced"),
) -> Dict[str, Any]:
    """Summary only — benefits from the same store cache."""
    return analyze_store(store_id, business_objective)


@router.delete("/cache")
def clear_cache() -> Dict[str, Any]:
    """Dev endpoint — clears all cached results."""
    count = len(_store_cache)
    _store_cache.clear()
    _DataCache.invalidate()
    logger.info("Cache cleared (%d store entries + CSV cache invalidated)", count)
    return {
        "cleared": count,
        "message": "Store result cache and CSV cache cleared. Next request re-runs the pipeline.",
    }


# ---------------------------------------------------------------------------
# WebSocket — real-time inventory push
# ---------------------------------------------------------------------------

@router.websocket("/ws/{store_id}")
async def ws_inventory(
    websocket: WebSocket,
    store_id: str,
    business_objective: str = Query(default="balanced"),
) -> None:
    await websocket.accept()
    canonical_id = to_canonical_store_id(store_id)
    frontend_id = to_frontend_store_id(canonical_id)
    logger.info("Inventory WebSocket connected: %s -> %s (objective: %s)", store_id, canonical_id, business_objective)
    # Register this connection
    if canonical_id not in _active_ws_connections:
        _active_ws_connections[canonical_id] = []
    _active_ws_connections[canonical_id].append((websocket, business_objective))
    try:
        # Send initial snapshot immediately
        try:
            loop = asyncio.get_event_loop()
            payload = await loop.run_in_executor(
                None,
                lambda: analyze_store(frontend_id, business_objective),
            )
            await websocket.send_json({
                "type": "inventory_update",
                "store_id": frontend_id,
                "business_objective": business_objective,
                **payload,
            })
            logger.info("Sent initial snapshot to WebSocket: %s", frontend_id)
        except Exception as exc:
            logger.error("ws_inventory: initial snapshot failed: %s", exc)

        # Keep connection alive — broadcasts happen via invalidate_store()
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_json({
                    "type":      "heartbeat",
                    "store_id":  frontend_id,
                    "timestamp": time.time(),
                })
            except Exception:
                break

    except WebSocketDisconnect:
        logger.info("Inventory WebSocket disconnected: %s", frontend_id)
    except Exception as exc:
        logger.error("Inventory WebSocket error (%s): %s", frontend_id, exc)
    finally:
        # Unregister this connection
        if canonical_id in _active_ws_connections:
            _active_ws_connections[canonical_id] = [
                (ws, obj) for ws, obj in _active_ws_connections[canonical_id]
                if ws != websocket
            ]
            if not _active_ws_connections[canonical_id]:
                del _active_ws_connections[canonical_id]