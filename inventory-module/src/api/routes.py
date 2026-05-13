"""
Inventory API Routes
====================
Multi-store aware. SKUs come from BOTH stock_history AND product_master
(filtered to SKUs that actually have sales in the store) so all ~4 000
products are reachable, not just the handful that appear in the
stock-history snapshot.
"""

from __future__ import annotations

import logging
import time
import asyncio
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from config.settings import STOCK_HISTORY_PATH, PRODUCT_MASTER_PATH
from src.services.orchestrator import create_orchestrator
from src.tools.internal.stock_tools import _DataCache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inventory"])

_orchestrator      = None
_orchestrator_fast = None

# ---------------------------------------------------------------------------
# Result cache
# ---------------------------------------------------------------------------

CACHE_TTL = 1200  # 20 min
_store_cache: Dict[str, Dict[str, Any]] = {}

# Per-store pipeline lock — only one pipeline run per store at a time.
# Concurrent callers (WS + HTTP poll) block here and share the result.
import threading
_pipeline_locks: Dict[str, threading.Lock] = {}
_pipeline_locks_guard: threading.Lock = threading.Lock()

def _get_store_lock(store_id: str) -> threading.Lock:
    with _pipeline_locks_guard:
        if store_id not in _pipeline_locks:
            _pipeline_locks[store_id] = threading.Lock()
        return _pipeline_locks[store_id]


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

_active_ws_connections: Dict[str, List[tuple[WebSocket, str]]] = {}
_pending_broadcasts: Dict[str, asyncio.Task] = {}
BROADCAST_DEBOUNCE_SECONDS = 2.0


async def _broadcast_to_store(store_id: str, message: dict) -> None:
    connections = _active_ws_connections.get(store_id, [])
    dead = []
    for ws, _ in connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections[:] = [(w, obj) for w, obj in connections if w != ws]


async def _broadcast_stock_delta(store_id: str, sku: str, new_stock: float,
                                  risk_level: str, days_of_stock: float,
                                  coverage_ratio: float, risk_rationale: str) -> None:
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
    await asyncio.sleep(BROADCAST_DEBOUNCE_SECONDS)

    connections = _active_ws_connections.get(store_id, [])
    if not connections:
        logger.info("No WebSocket connections for %s, skipping broadcast", store_id)
        return

    objectives = set(obj for _, obj in connections)

    for business_objective in objectives:
        try:
            loop = asyncio.get_event_loop()
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

    _pending_broadcasts.pop(store_id, None)


def invalidate_store(store_id: str, sku: str = None, new_stock: float = None) -> None:
    keys_to_drop = [k for k in _store_cache if k.startswith(f"{store_id}::")]
    for k in keys_to_drop:
        del _store_cache[k]

    if keys_to_drop:
        logger.info("invalidate_store(%s): dropped %d cache entries", store_id, len(keys_to_drop))

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            def _schedule():
                if sku is not None and new_stock is not None:
                    risk_level, days_of_stock, coverage_ratio, rationale = \
                        _quick_risk(store_id, sku, new_stock)
                    asyncio.create_task(_broadcast_stock_delta(
                        store_id, sku, new_stock,
                        risk_level, days_of_stock, coverage_ratio, rationale,
                    ))

                old_task = _pending_broadcasts.get(store_id)
                if old_task and not old_task.done():
                    old_task.cancel()
                task = asyncio.create_task(_push_update_to_store(store_id))
                _pending_broadcasts[store_id] = task

            loop.call_soon_threadsafe(_schedule)
    except RuntimeError:
        logger.warning("No event loop available for WebSocket broadcast")


# ---------------------------------------------------------------------------
# SKU resolution — MULTI-SOURCE
# ---------------------------------------------------------------------------

def _resolve_skus_for_store(store_id: str) -> List[str]:
    """
    Return all SKUs that are meaningful to analyse for a given store.

    Priority / sources (merged, deduplicated):
      1. stock_history.csv  — SKUs with a current stock snapshot for this store
      2. sales_history.csv  — SKUs ever sold in this store (catches items not yet
                              in the stock snapshot but moving off the shelf)
      3. product_master.csv — Only as a cross-reference; we do NOT analyse every
                              product in the master list for a store that has no
                              sales data (that would be thousands of zero-demand
                              rows that overwhelm the pipeline).

    If the store is not found in either sales or stock data, raises 404.
    """
    skus: set[str] = set()

    # ── 1. Stock snapshot ────────────────────────────────────────────────────
    try:
        stock_df = _DataCache.stock()
        store_stock = stock_df[stock_df["store_id"] == store_id]
        # ✅ Convert to strings to avoid integer SKUs
        skus.update(store_stock["sku"].dropna().astype(str).unique().tolist())
        logger.info("Store %s: %d SKUs from stock_history", store_id, len(skus))
    except Exception as exc:
        logger.warning("Could not read stock_history for %s: %s", store_id, exc)

    # ── 2. Sales history ─────────────────────────────────────────────────────
    try:
        sales_df = _DataCache.sales()   # raises if missing — caught below
        if "store_id" in sales_df.columns:
            store_sales = sales_df[sales_df["store_id"] == store_id]
        else:
            store_sales = sales_df      # single-store CSV without store_id column
        # ✅ Convert to strings to avoid integer SKUs
        sales_skus = store_sales["sku"].dropna().astype(str).unique().tolist()
        before = len(skus)
        skus.update(sales_skus)
        logger.info(
            "Store %s: +%d SKUs from sales_history (total %d)",
            store_id, len(skus) - before, len(skus)
        )
    except Exception as exc:
        logger.warning("Could not read sales_history for %s: %s", store_id, exc)

    # ── 3. Guard ─────────────────────────────────────────────────────────────
    if not skus:
        # Last resort: check product_master but warn loudly — this will be slow
        try:
            pm = _DataCache.product()
            # ✅ Convert to strings to avoid integer SKUs
            all_pm_skus = pm["sku"].dropna().astype(str).unique().tolist()
            logger.warning(
                "Store %s has NO stock/sales data. Falling back to full product_master "
                "(%d SKUs). This will be very slow — check your store_id.",
                store_id, len(all_pm_skus)
            )
            # Cap at 200 so we don't hang the server
            skus.update(all_pm_skus[:200])
        except Exception as exc:
            logger.error("product_master also unavailable: %s", exc)

    if not skus:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No SKUs found for store '{store_id}'. "
                f"Available stores: {_list_store_ids()}"
            )
        )

    return sorted(skus)


def _list_store_ids() -> List[str]:
    """Return all store IDs known across stock + sales data."""
    ids: set[str] = set()
    try:
        df = _DataCache.stock()
        # ✅ Convert to strings to avoid integer store IDs
        ids.update(df["store_id"].dropna().astype(str).unique().tolist())
    except Exception:
        pass
    try:
        df = _DataCache.sales()
        if "store_id" in df.columns:
            # ✅ Convert to strings to avoid integer store IDs
            ids.update(df["store_id"].dropna().astype(str).unique().tolist())
    except Exception:
        pass
    return sorted(ids)


# ---------------------------------------------------------------------------
# Quick risk (for stock_delta broadcast — no LLM)
# ---------------------------------------------------------------------------

def _quick_risk(store_id: str, sku: str, current_stock: float):
    try:
        sku_str = str(sku)  # Ensure string for comparison
        
        prod_df = _DataCache.product()
        # Convert sku column to string for comparison
        prod_df['sku'] = prod_df['sku'].astype(str)
        rows = prod_df[prod_df["sku"] == sku_str]
        if rows.empty:
            return "ok", 999.0, 5.0, ""
        p = rows.iloc[0]

        lead_time_avg = float(p.get("lead_time_days", 7))
        lead_time_std = float(p.get("lead_time_std", 0))

        forecast_df = _DataCache.forecast()
        if "sku" in forecast_df.columns:
            # Convert sku column to string for comparison
            forecast_df['sku'] = forecast_df['sku'].astype(str)
            forecast_df = forecast_df[forecast_df["sku"] == sku_str]
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
                f"({lead_time_avg:.0f}d ± {lead_time_std:.1f}d)."
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


# ---------------------------------------------------------------------------
# Orchestrator helpers
# ---------------------------------------------------------------------------

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = create_orchestrator()
    return _orchestrator


def get_orchestrator_fast():
    global _orchestrator_fast
    if _orchestrator_fast is None:
        _orchestrator_fast = create_orchestrator(use_llm=False)
    return _orchestrator_fast


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    sku: str
    store_id: str = Field(default="I63")
    business_objective: str = Field(default="balanced")


class BatchAnalyzeRequest(BaseModel):
    store_id: str = Field(default="I63")
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
    store_id = result.get("store_id", "I63")

    live_stock    = _DataCache.get_current_stock(sku, store_id)
    report_stock  = stock.get("current_stock", 0)
    current_stock = live_stock if live_stock > 0 else report_stock

    avg_daily  = forecast.get("avg_daily_demand", 1) or 1
    lead_time  = stock.get("lead_time_avg_days", 7)

    days_remain = current_stock / avg_daily if avg_daily > 0 else 0

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
        "name":             str(pi.get("name", "") or sku).strip() or str(sku),
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

@router.get("/stores")
def list_stores() -> Dict[str, Any]:
    """
    Return all store IDs found in stock_history + sales_history,
    together with a display label where available.
    """
    ids = _list_store_ids()
    # Try to attach human-readable names from boutique / sales data
    names: Dict[str, str] = {}
    try:
        sales_df = _DataCache.sales()
        if {"store_id", "store_name"}.issubset(sales_df.columns):
            mapping = (
                sales_df[["store_id", "store_name"]]
                .drop_duplicates("store_id")
                .set_index("store_id")["store_name"]
                .to_dict()
            )
            names.update(mapping)
    except Exception:
        pass

    stores = [
        {"id": sid, "name": names.get(sid, sid)}
        for sid in ids
    ]
    return {"stores": stores, "store_ids": ids}


@router.get("/skus")
def list_skus(store_id: Optional[str] = Query(default=None)) -> Dict[str, List[str]]:
    """
    Return SKUs available for a store (stock + sales), or all SKUs if
    no store_id is given.
    """
    if store_id:
        return {"skus": _resolve_skus_for_store(store_id)}

    # All known SKUs
    all_skus: set[str] = set()
    try:
        # ✅ Convert to strings to avoid integer SKUs
        all_skus.update(_DataCache.stock()["sku"].dropna().astype(str).unique().tolist())
    except Exception:
        pass
    try:
        # ✅ Convert to strings to avoid integer SKUs
        all_skus.update(_DataCache.sales()["sku"].dropna().astype(str).unique().tolist())
    except Exception:
        pass
    return {"skus": sorted(all_skus)}


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
    fast: bool = False,
    # ── Pagination ────────────────────────────────────────────────────────
    # page     : 1-based page index
    # page_size: items per page (0 = return all, use with care on 4 000 SKUs)
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=0, le=500),
) -> Dict[str, Any]:
    """
    Full store analysis with result caching and pagination.

    With 100+ SKUs the pipeline can take 15-20 s on first call.
    Subsequent calls within CACHE_TTL (20 min) are instant.
    force_refresh=True always re-runs the pipeline.
    fast=True uses the rule-based orchestrator (no LLM) for WS broadcasts.

    Pagination:
      GET /store/I63?page=1&page_size=100   → first 100 SKUs
      GET /store/I63?page=2&page_size=100   → next 100
      GET /store/I63?page_size=0            → all (slow — avoid on large stores)
    """
    # Check cache first (no lock needed for read)
    if not force_refresh:
        cached = _get_cached(store_id, business_objective)
        if cached is not None:
            return _paginate(cached, page, page_size)

    # Acquire per-store lock so only ONE pipeline runs at a time.
    # Concurrent callers (WS background task + HTTP poll) will block here
    # and then immediately get the cache result once the first run completes.
    lock = _get_store_lock(f"{store_id}::{business_objective}")
    with lock:
        # Re-check cache inside the lock — another thread may have populated it
        cached = _get_cached(store_id, business_objective)
        if cached is not None:
            logger.info("Cache populated by concurrent run for %s — reusing", store_id)
            return _paginate(cached, page, page_size)

        skus = _resolve_skus_for_store(store_id)   # raises 404 if nothing found

        orchestrator = get_orchestrator_fast() if fast else get_orchestrator()
        logger.info(
            "Running pipeline for %s (%d SKUs) [fast=%s, objective=%s]...",
            store_id, len(skus), fast, business_objective,
        )
        results = orchestrator.analyze_batch(skus, store_id, business_objective)
        results = _enrich_with_product_master(results)

        items   = [_to_inventory_item(r) for r in results]
        alerts  = _build_alerts(items)
        payload = {
            "store_id":           store_id,
            "business_objective": business_objective,
            "total_skus":         len(items),
            "items":              items,
            "alerts":             alerts,
            "summary":            _build_summary(items),
        }

        _set_cache(store_id, business_objective, payload)
        return _paginate(payload, page, page_size)


def _paginate(payload: Dict[str, Any], page: int, page_size: int) -> Dict[str, Any]:
    """Slice `items` for the requested page; keep everything else intact."""
    if page_size == 0:
        # Caller explicitly requested all items — return as-is
        return {**payload, "page": 1, "page_size": 0, "total_pages": 1}

    all_items  = payload.get("items", [])
    total      = len(all_items)
    total_pages = max(1, -(-total // page_size))   # ceiling division
    page        = min(page, total_pages)
    start       = (page - 1) * page_size
    end         = start + page_size

    return {
        **payload,
        "items":       all_items[start:end],
        "page":        page,
        "page_size":   page_size,
        "total_pages": total_pages,
        "total_skus":  total,
    }


@router.get("/summary/{store_id}")
def get_summary(
    store_id: str,
    business_objective: str = Query(default="balanced"),
) -> Dict[str, Any]:
    """Summary only — benefits from the same store cache."""
    payload = analyze_store(store_id, business_objective, page=1, page_size=0)
    return payload["summary"]


@router.delete("/cache")
def clear_cache() -> Dict[str, Any]:
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
    logger.info(
        "Inventory WebSocket connected: %s (objective: %s)", store_id, business_objective
    )

    if store_id not in _active_ws_connections:
        _active_ws_connections[store_id] = []
    _active_ws_connections[store_id].append((websocket, business_objective))

    try:
        # ── Initial snapshot strategy ────────────────────────────────────────
        # The pipeline can take 15-20s on a cold start. We don't block the WS
        # coroutine waiting for it. Instead:
        #   1. If the cache is already warm, send immediately.
        #   2. If not, send a "loading" heartbeat so the frontend knows we're alive,
        #      then poll the cache every 3s until it's populated (the HTTP endpoint
        #      or a concurrent WS will trigger the pipeline).
        # This avoids running the pipeline twice (HTTP fallback + WS) in parallel.

        cached = _get_cached(store_id, business_objective)
        if cached is not None:
            # Cache warm — send immediately
            await websocket.send_json({
                "type": "inventory_update",
                "store_id": store_id,
                "business_objective": business_objective,
                **cached,
            })
            logger.info("Sent cached snapshot to WebSocket: %s (%d items)", store_id, len(cached.get("items", [])))
        else:
            # Cache cold — tell the frontend to use HTTP polling and wait
            await websocket.send_json({
                "type": "inventory_loading",
                "store_id": store_id,
                "message": "Pipeline running — use HTTP polling, will push when ready",
            })
            logger.info("Cache cold for %s — WS will push snapshot when pipeline completes", store_id)

            # Kick off the pipeline in background (non-blocking) so it populates the cache.
            # The HTTP poller in the frontend will pick it up; we also push via WS when done.
            async def _run_pipeline_and_push():
                try:
                    loop = asyncio.get_event_loop()
                    payload = await loop.run_in_executor(
                        None,
                        lambda: analyze_store(
                            store_id, business_objective,
                            force_refresh=False, fast=False,
                            page=1, page_size=0,
                        ),
                    )
                    # Push to all connected clients for this store
                    await _broadcast_to_store(store_id, {
                        "type": "inventory_update",
                        "store_id": store_id,
                        "business_objective": business_objective,
                        **payload,
                    })
                    logger.info(
                        "Pipeline done for %s — pushed %d items via WS",
                        store_id, len(payload.get("items", []))
                    )
                except Exception as exc:
                    logger.error("Background pipeline failed for %s: %s", store_id, exc)

            asyncio.create_task(_run_pipeline_and_push())

        # ── Heartbeat loop ───────────────────────────────────────────────────
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_json({
                    "type":      "heartbeat",
                    "store_id":  store_id,
                    "timestamp": time.time(),
                })
            except Exception:
                break

    except WebSocketDisconnect:
        logger.info("Inventory WebSocket disconnected: %s", store_id)
    except Exception as exc:
        logger.error("Inventory WebSocket error (%s): %s", store_id, exc)
    finally:
        if store_id in _active_ws_connections:
            _active_ws_connections[store_id] = [
                (ws, obj) for ws, obj in _active_ws_connections[store_id]
                if ws != websocket
            ]
            if not _active_ws_connections[store_id]:
                del _active_ws_connections[store_id]