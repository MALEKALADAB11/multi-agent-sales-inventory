"""
Inventory API Routes
====================
Exposes the InventoryAnalysisAgent over HTTP.

What this layer does:
  - Translates orchestrator output into the shape Angular expects
  - Enriches with product_master (name, category)
  - Derives alerts from risk classification (condition only, no actions)
  - Caches full store analysis results to avoid re-running the pipeline
    on every frontend request

What this layer intentionally does NOT do:
  - Generate order recommendations  → Decision Agent's job
  - Adjust formula_order_qty for context → Context Agent's job
  - Prescribe timing or urgency → Decision Agent's job

Mount in api_server.py with:
    app.include_router(router, prefix="/api/inventory")

Endpoints
---------
GET    /api/inventory/skus?store_id=
GET    /api/inventory/stores
GET    /api/inventory/store/{store_id}?force_refresh=false
GET    /api/inventory/summary/{store_id}
POST   /api/inventory/analyze
DELETE /api/inventory/cache    ← dev only: clears the result cache
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from config.settings import STOCK_HISTORY_PATH, PRODUCT_MASTER_PATH
from src.services.orchestrator import create_orchestrator

# _DataCache is already loaded by stock_tools at first use.
# We import it here so /skus and /stores read from memory, not disk.
from src.tools.internal.stock_tools import _DataCache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inventory"])

_orchestrator = None

# ---------------------------------------------------------------------------
# Result cache
#
# Why: The pipeline costs ~1,100 tokens × 30 SKUs = ~33,000 tokens per store
# call. Free Groq tier is 100k tokens/day — that's only 3 full runs per day
# without caching. With caching the pipeline runs once per TTL window and
# every subsequent frontend request (page refresh, navigation, hot reload)
# is served instantly at zero token cost.
#
# CACHE_TTL: 20 minutes is a good default for development.
#   - Long enough to survive Angular hot reloads and page navigations.
#   - Short enough that you don't wait too long after updating CSV data.
#   - Call DELETE /api/inventory/cache to force a refresh immediately.
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


def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = create_orchestrator()
    return _orchestrator


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
    # Use _DataCache so we don't re-read the CSV on every call
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

    sku           = result["sku"]
    current_stock = stock.get("current_stock", 0)
    avg_daily     = forecast.get("avg_daily_demand", 1) or 1
    lead_time     = stock.get("lead_time_avg_days", 7)
    days_remain   = metrics.get("days_of_stock_remaining", 0)

    risk_level_raw = risk.get("level", "MEDIUM")
    risk_level     = RISK_MAP.get(risk_level_raw, "medium")
    risk_score     = RISK_SCORE_MAP.get(risk_level, 0.5)

    coverage_ratio = round(days_remain / lead_time, 2) if lead_time else 0

    trend_raw = forecast.get("trend_direction", "stable").lower()
    trend = (
        "up"   if "up"   in trend_raw or "increas" in trend_raw else
        "down" if "down" in trend_raw or "decreas" in trend_raw else
        "stable"
    )

    return {
        "id":                   sku,
        "sku":                  sku,
        "name":                 pi.get("name", sku),
        "category":             pi.get("category", "Unknown"),
        "store_id":             result["store_id"],
        "business_objective":   result["business_objective"],

        "stock":                int(current_stock),
        "stockMin":             int(metrics.get("reorder_point", 0)),
        "stockMax":             int(stock.get("moq", 0) + metrics.get("safety_stock", 0)),
        "unitCost":             pi.get("unit_cost", stock.get("unit_cost", 0)),
        "moq":                  pi.get("moq", stock.get("moq", 0)),

        "demandForecast24h":    round(avg_daily, 1),
        "coverageRatio":        coverage_ratio,
        "daysOfStock":          round(days_remain, 1),
        "leadTimeDays":         lead_time,

        "riskLevel":            risk_level,
        "riskScore":            risk_score,
        "overstockFlag":        risk.get("overstock_flag", False),
        "riskRationale":        risk.get("rationale", ""),

        "reorderPoint":             int(metrics.get("reorder_point", 0)),
        "safetyStock":              int(metrics.get("safety_stock", 0)),
        "safetyStockCostDt":        metrics.get("safety_stock_cost_dt", 0),
        "eoq":                      int(metrics.get("eoq", 0)),
        # formula_order_qty = max(EOQ, MOQ) — mathematical floor, NOT a decision.
        # Decision Agent sets the actual order quantity.
        "formulaOrderQty":          int(metrics.get("formula_order_qty", 0)),
        "totalReplenishmentCost":   metrics.get("total_replenishment_cost", 0),
        "holdingCostPerCycleDt":    metrics.get("holding_cost_per_cycle_dt", 0),
        "effectiveServiceLevel":    metrics.get("effective_service_level", 0),
        "zScore":                   metrics.get("z_score", 0),

        "moqIsBinding":             constr.get("moq_is_binding", False),
        "moqBindingNote":           constr.get("moq_binding_note", ""),
        "highCostFlag":             constr.get("high_cost_flag", False),
        "highHoldingFlag":          constr.get("high_holding_flag", False),

        "analystNote":              report.get("objective_note", ""),

        "trend":                    trend,
        "confidence":               metrics.get("effective_service_level", 0.9),
        "lastUpdated":              pd.Timestamp.now().strftime("%I:%M %p"),

        # Decision Agent placeholders — do NOT populate here
        "recommendation":           None,
        "recommendationDetail":     None,
        "finalOrderQty":            None,
        "orderTiming":              None,
    }


# ---------------------------------------------------------------------------
# Alert derivation
# ---------------------------------------------------------------------------

def _build_alerts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts = []
    for item in items:
        if item.get("error"):
            continue

        risk  = item["riskLevel"]
        sku   = item["sku"]
        name  = item["name"]
        stock = item["stock"]
        days  = item["daysOfStock"]
        lt    = item["leadTimeDays"]

        if risk == "critical":
            alerts.append({
                "id":      f"alert-{sku}-crit",
                "type":    "rupture",
                "sku":     sku,
                "urgency": "critical",
                "message": (
                    f"{name} — {stock} units in stock, {days:.1f}d remaining "
                    f"vs {lt:.0f}d lead time"
                ),
                "action":  None,  # Decision Agent sets this
                "time":    item["lastUpdated"],
            })
        elif risk == "high":
            alert_type = "overstock" if item.get("overstockFlag") else "rupture"
            alerts.append({
                "id":      f"alert-{sku}-high",
                "type":    alert_type,
                "sku":     sku,
                "urgency": "high",
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
    # Use _DataCache — same data, no extra disk read
    df = _DataCache.stock()
    if store_id:
        df = df[df["store_id"] == store_id]
    return {"skus": sorted(df["sku"].unique().tolist())}


@router.get("/stores")
def list_stores() -> Dict[str, List[str]]:
    # Use _DataCache — same data, no extra disk read
    df = _DataCache.stock()
    return {"stores": sorted(df["store_id"].unique().tolist())}


@router.post("/analyze")
def analyze_single(req: AnalyzeRequest) -> Dict[str, Any]:
    """
    Single-SKU on-demand analysis. Not cached.
    Costs ~1,100 tokens per call — use sparingly on the free tier.
    """
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
) -> Dict[str, Any]:
    """
    Full store analysis with result caching.

    First call  : runs the full pipeline (~10-30s), result cached for CACHE_TTL.
    Later calls : served from cache instantly, zero token cost.
    force_refresh=true : bypasses cache, re-runs pipeline (costs tokens).

    Use force_refresh only from the terminal after changing CSV data:
      curl "http://localhost:8000/api/inventory/store/STORE-001?force_refresh=true"
    Don't wire it to a frontend button — it burns your daily token budget.
    """
    if not force_refresh:
        cached = _get_cached(store_id, business_objective)
        if cached is not None:
            return cached

    # Cache miss or forced refresh
    df   = _DataCache.stock()
    skus = sorted(df[df["store_id"] == store_id]["sku"].unique().tolist())
    if not skus:
        raise HTTPException(status_code=404, detail=f"No SKUs found for store '{store_id}'")

    logger.info("Running pipeline for %s (%d SKUs)...", store_id, len(skus))
    results = get_orchestrator().analyze_batch(skus, store_id, business_objective)
    results = _enrich_with_product_master(results)

    items   = [_to_inventory_item(r) for r in results]
    alerts  = _build_alerts(items)
    payload = {
        "store_id":           store_id,
        "business_objective": business_objective,
        "items":              items,
        "alerts":             alerts,
        "summary":            _build_summary(items),
    }

    _set_cache(store_id, business_objective, payload)
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
    """
    Dev endpoint — clears all cached results so the next store request
    re-runs the full pipeline.

    Call this after:
      - Updating CSV data files
      - Changing analysis agent logic
      - Testing a different business objective

    curl -X DELETE http://localhost:8000/api/inventory/cache
    """
    count = len(_store_cache)
    _store_cache.clear()
    _DataCache.invalidate()   # also drop the CSV cache so fresh files are read
    logger.info("Cache cleared (%d store entries + CSV cache invalidated)", count)
    return {
        "cleared": count,
        "message": "Store result cache and CSV cache cleared. Next request re-runs the pipeline.",
    }