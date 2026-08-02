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
import os
import time
import asyncio
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.inventory.services.orchestrator import create_orchestrator
from app.inventory.tools.internal.stock_tools import _DataCache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inventory"])

_orchestrator      = None
_orchestrator_fast = None

# ---------------------------------------------------------------------------
# Result cache
# ---------------------------------------------------------------------------

CACHE_TTL = 3600  # 1 hour — keeps data through a full work session without forcing a reload
_store_cache: Dict[str, Dict[str, Any]] = {}

import decimal as _decimal

def _json_safe(obj: Any) -> Any:
    """Recursively convert Decimal/non-serializable types for JSON."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, _decimal.Decimal):
        return float(obj)
    return obj

# ── Demo / quality settings ──────────────────────────────────────────────────
# Cap the number of SKUs analyzed per store. This directly controls:
#   - pipeline duration (fewer SKUs = faster)
#   - alert count (fewer ghost products = fewer phantom criticals)
# Set to 0 to disable the cap (process all SKUs).
#
# NOTE: this was previously 0 (uncapped). With `fast=True` already using a
# non-LLM orchestrator, the dominant cost of GET /store/{store_id} is simply
# "per-SKU pipeline work x SKU count" (DB round-trips + Langfuse spans per
# SKU across nested ThreadPoolExecutors). For stores with hundreds/thousands
# of SKUs this alone can blow past any HTTP timeout and, at high enough
# thread concurrency, starve the event loop's GIL time badly enough that
# unrelated WebSocket handshakes time out too. Capping bounds worst-case
# latency regardless of store size. Raise this (or pass force_refresh with a
# dedicated "full" endpoint) if you need the complete, uncapped view.
DEMO_SKU_CAP = int(os.getenv("INVENTORY_SKU_CAP", "80"))

# Per-store pipeline lock — only one pipeline run per store at a time.
# Concurrent callers (WS + HTTP poll) block here and share the result.
import threading
from app.core.config import DEFAULT_STORE_ID
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
    safe_message = _json_safe(message)
    for ws, _ in connections:
        try:
            await ws.send_json(safe_message)
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
                    store_id, obj, force_refresh=True, fast=True,
                    page=1, page_size=0,
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
    if sku is None or new_stock is None:
        keys_to_drop = [k for k in _store_cache if k.startswith(f"{store_id}::")]
        for k in keys_to_drop:
            del _store_cache[k]
        if keys_to_drop:
            logger.info("invalidate_store(%s): dropped %d cache entries", store_id, len(keys_to_drop))
    else:
        patched = 0
        for k, entry in _store_cache.items():
            if not k.startswith(f"{store_id}::"):
                continue
            for item in entry["data"].get("items", []):
                if item.get("sku") == sku:
                    item["stock"] = new_stock
                    patched += 1
                    break
        if patched:
            logger.info("invalidate_store(%s): patched sku=%s stock=%s in %d cache entr%s",
                         store_id, sku, new_stock, patched, "y" if patched == 1 else "ies")

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
# SKU quality filter — removes ghost products for demo stability
# ---------------------------------------------------------------------------

# Cache DB SKU set so we only query once per process lifetime
_db_sku_cache: Optional[set] = None

def _get_db_skus() -> Optional[set]:
    """
    Returns the set of SKUs present in inventory.products (DB).
    Cached after first call — only queries DB once.
    Returns None if DB is unavailable (fallback to CSV).
    """
    global _db_sku_cache
    if _db_sku_cache is not None:
        return _db_sku_cache
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            import psycopg2.extras
            with conn.cursor() as cur:
                cur.execute("SELECT sku FROM inventory.products")
                rows = cur.fetchall()
                _db_sku_cache = {str(r[0]) for r in rows}
                logger.info("DB SKU cache: %d SKUs in inventory.products", len(_db_sku_cache))
                return _db_sku_cache
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("_get_db_skus failed (%s) — will fallback to CSV filter", exc)
        return None


def _filter_quality_skus(skus: List[str], store_id: str) -> List[str]:
    """
    Keep only SKUs that exist in inventory.products (DB).
    This guarantees:
      - inventory.stock_levels has a row for each SKU (seeded by init_stock_levels.py)
      - inventory.alerts FK constraint is satisfied on insert
      - DB stock reads work in _to_inventory_item

    Falls back to CSV-based filter (product_master.csv) if DB is unavailable,
    which also drops Unknown-named products.
    """
    # ── Primary: intersect with inventory.products ─────────────────────────────────────────────
    db_skus = _get_db_skus()
    if db_skus:
        good = [s for s in skus if s in db_skus]
        removed = len(skus) - len(good)
        logger.info(
            "Quality filter (DB): %d SKUs in inventory.products for %s | dropped %d absent",
            len(good), store_id, removed,
        )
        if good:
            return good
        logger.warning("DB filter removed all SKUs for %s — falling back to CSV", store_id)

    # ── Fallback: CSV product_master, also drops Unknown names ─────────────────────
    try:
        pm = _DataCache.product()
        pm = pm.copy()
        pm["sku"] = pm["sku"].astype(str)
        bad_names = {"unknown", "n/a", "nan", "none", "", "."}
        name_col = "product_name" if "product_name" in pm.columns else None

        good = []
        sku_set = set(pm["sku"].unique())
        for s in skus:
            if s not in sku_set:
                continue
            if name_col:
                name_val = pm.loc[pm["sku"] == s, name_col]
                if name_val.empty:
                    continue
                name = str(name_val.iloc[0]).strip().lower()
                if name in bad_names:
                    continue
                # Log SKU-as-name products instead of silently dropping them
                if name == s.lower():
                    logger.debug("Quality filter: SKU %s has sku-as-name — kept but flagged", s)
            good.append(s)

        removed = len(skus) - len(good)
        logger.info(
            "Quality filter (CSV fallback): %d kept for %s | dropped %d",
            len(good), store_id, removed,
        )
        return good if good else skus
    except Exception as exc:
        logger.warning("_filter_quality_skus CSV fallback failed (%s)", exc)
        return skus


def _top_n_by_sales(skus: List[str], store_id: str, n: int) -> List[str]:
    """
    Keep the N SKUs with the highest sales volume in this store.
    Unseen SKUs (no sales history) are appended at the end up to the cap.
    """
    try:
        sales_df = _DataCache.sales()
        if "store_id" in sales_df.columns:
            store_sales = sales_df[sales_df["store_id"] == store_id].copy()
        else:
            store_sales = sales_df.copy()

        store_sales["sku"] = store_sales["sku"].astype(str)
        sku_set = set(skus)

        volume = (
            store_sales[store_sales["sku"].isin(sku_set)]
            .groupby("sku")["quantity_sold"]
            .sum()
            .sort_values(ascending=False)
        )

        top     = [s for s in volume.index.tolist() if s in sku_set]
        no_data = [s for s in skus if s not in set(top)]
        result  = (top + no_data)[:n]

        logger.info(
            "Top-N filter: kept %d/%d SKUs by sales volume for %s",
            len(result), len(skus), store_id,
        )
        return result
    except Exception as exc:
        logger.warning("_top_n_by_sales failed (%s) — returning first %d SKUs", exc, n)
        return skus[:n]


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
            detail=f"No SKUs found for store '{store_id}'. Available: {_list_store_ids()}"
        )

    all_skus = sorted(skus)

    # ── Quality filter: drop ghost products (no valid name in product_master) ─
    all_skus = _filter_quality_skus(all_skus, store_id)

    if not all_skus:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No valid SKUs found for store '{store_id}' after quality filter. "
                f"Check that product_master.csv has product_name populated."
            ),
        )

    # ── Demo cap: keep top N by sales volume ──────────────────────────────────
    if DEMO_SKU_CAP > 0 and len(all_skus) > DEMO_SKU_CAP:
        all_skus = _top_n_by_sales(all_skus, store_id, DEMO_SKU_CAP)

    return all_skus


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
    """
    Fast risk approximation for WebSocket stock_delta broadcasts.
    This is NOT the authoritative risk level — the analysis agent's
    risk_assessment (with LLM validation) is the source of truth.
    Only used when agent output is unavailable (e.g., during a sale event).
    """
    try:
        sku_str = str(sku)

        # ── Product data: DB first, CSV fallback ─────────────────────────────
        lead_time_avg = 7.0
        lead_time_std = 0.0
        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo
            db_product = SyncInventoryRepo.get_product(sku_str)
            if db_product:
                lead_time_avg = float(db_product.get("lead_time_days") or 7)
                lead_time_std = float(db_product.get("lead_time_std")  or 0)
            else:
                raise ValueError("not in DB")
        except Exception:
            prod_df = _DataCache.product()
            prod_df["sku"] = prod_df["sku"].astype(str)
            rows = prod_df[prod_df["sku"] == sku_str]
            if not rows.empty:
                p = rows.iloc[0]
                lead_time_avg = float(p.get("lead_time_days", 7))
                lead_time_std = float(p.get("lead_time_std",  0))

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
                f"Stock ({days_remaining:.1f}j) épuisé avant la prochaine livraison "
                f"(délai fournisseur {lead_time_avg:.0f}j). Rupture imminente."
            )
        elif days_remaining < lead_time_avg + lt_var:
            risk_level = "high"
            rationale = (
                f"Stock ({days_remaining:.1f}j) dans la fenêtre de variabilité du délai "
                f"fournisseur ({lead_time_avg:.0f}j ± {lead_time_std:.1f}j)."
            )
        elif days_remaining < lead_time_avg * 2.5:
            risk_level = "medium"
            rationale = f"Stock ({days_remaining:.1f}j) inférieur à 2,5× le délai fournisseur. À surveiller."
        else:
            risk_level = "ok"
            rationale = f"Stock ({days_remaining:.1f}j) supérieur à 2,5× le délai fournisseur. Bien couvert."

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
        # FIXED (was use_llm=False — see git history): this orchestrator is
        # the genuinely LLM-backed one. It's used by:
        #   - POST /analyze                    (single-SKU on-demand deep dive)
        #   - analyze_store(..., fast=False)    (opt-in full-LLM batch run)
        # Real LLM latency applies here (analysis + context agents on the
        # "fast" tier, decision agent on the "smart" tier, with the
        # OpenRouter → Groq → Ollama fallback chain in llm_factory.py).
        # get_orchestrator_fast() below stays rule-based for the default
        # batch/dashboard load and WS broadcasts, where instant response
        # matters more than per-SKU reasoning depth.
        _orchestrator = create_orchestrator(use_llm=True)
    return _orchestrator


def get_orchestrator_fast():
    global _orchestrator_fast
    if _orchestrator_fast is None:
        # Intentionally rule-based: this is the default path for the store
        # dashboard (100+ SKUs) and WS broadcasts, where an instant response
        # matters more than per-SKU LLM reasoning. Pass fast=False on the
        # batch endpoint (see analyze_store) to opt into the full-LLM
        # orchestrator above instead.
        # NB: create_orchestrator n'a pas de paramètre `fast` — le mode rapide
        # se choisit ici, par use_llm=False, et côté appelant par
        # analyze_store(fast=...) qui sélectionne l'un des deux singletons.
        _orchestrator_fast = create_orchestrator(use_llm=False)
    return _orchestrator_fast


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    sku: str
    store_id: str = Field(default_factory=lambda: DEFAULT_STORE_ID)
    business_objective: str = Field(default="balanced")


class BatchAnalyzeRequest(BaseModel):
    store_id: str = Field(default_factory=lambda: DEFAULT_STORE_ID)
    business_objective: str = Field(default="balanced")
    skus: Optional[List[str]] = None


class JudgeRequest(BaseModel):
    """Évaluation qualité (LLM-as-judge) d'une reco déjà produite par /analyze.
    Le frontend renvoie le `raw` et l'`item` reçus de /analyze : on ne rejoue
    pas le pipeline, on ne fait que l'appel juge (rapide) sur ces données."""
    raw:  Dict[str, Any]
    item: Dict[str, Any]


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
        pm.index = pm.index.astype(str)
    except Exception as exc:
        logger.warning("Could not load product_master for enrichment: %s", exc)
        return results

    enriched = []
    for r in results:
        sku = str(r.get("sku", ""))
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


def _to_inventory_item(
    result: Dict[str, Any],
    preloaded_stock: Dict[str, int] = None,
    product_lookup: Dict[str, Any] = None,
) -> Dict[str, Any]:
    if "error" in result:
        return {"sku": result.get("sku", ""), "error": result["error"]}

    report   = result.get("analysis_report", {})
    stock    = report.get("stock", {})
    forecast = report.get("forecast", {})
    metrics  = report.get("metrics", {})
    risk     = report.get("risk_assessment", {})
    constr   = report.get("constraints", {})
    pi       = result.get("product_info", {})

    sku      = result["sku"]
    store_id = result.get("store_id", DEFAULT_STORE_ID)

    # ── Current stock: preloaded batch dict → mem override → DB → report ──
    # Batch dict is populated by analyze_store before the loop (1 query total).
    # Individual DB call only fires for single-SKU analyze_single path.
    report_stock = stock.get("current_stock", 0)
    db_stock     = None

    mem_override = _DataCache._stock_overrides.get((sku, store_id))
    if mem_override is not None:
        current_stock = mem_override          # live sale override takes priority
    elif preloaded_stock is not None:
        current_stock = float(preloaded_stock.get(sku, report_stock))
    else:
        # Single-SKU path: hit DB directly
        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo
            db_row = SyncInventoryRepo.get_stock_level(sku, store_id)
            if db_row and db_row.get("stock_current") is not None:
                db_stock = int(db_row["stock_current"])
        except Exception:
            pass
        current_stock = db_stock if db_stock is not None else report_stock

    avg_daily  = forecast.get("avg_daily_demand", 1) or 1

    # ── Lead time: preloaded product_lookup dict → CSV filter fallback ────
    # product_lookup is built once before the loop from the cached CSV.
    # Without it, each call was filtering the full DataFrame — 110 times.
    lead_time     = 7.0
    lead_time_std = 0.0
    if product_lookup:
        prod_row = product_lookup.get(str(sku))
        if prod_row:
            lead_time     = float(prod_row.get("lead_time_days", 7) or 7)
            lead_time_std = float(prod_row.get("lead_time_std", 0) or 0)
    else:
        try:
            prod_rows = _DataCache.product()
            prod_rows = prod_rows[prod_rows["sku"] == sku]
            if not prod_rows.empty:
                lead_time     = float(prod_rows.iloc[0].get("lead_time_days", 7))
                lead_time_std = float(prod_rows.iloc[0].get("lead_time_std", 0))
        except Exception:
            pass

    days_remain = current_stock / avg_daily if avg_daily > 0 else 0

    # Use the agent's computed and LLM-validated risk level as the single source of truth
    # Fallback to _quick_risk only when agent risk_assessment is missing
    if risk and risk.get("level"):
        agent_risk_raw = risk["level"]
    else:
        # No agent risk available — fast fallback (should be rare: CSV-only mode)
        agent_risk_raw, _, _, _ = _quick_risk(store_id, sku, current_stock)
        agent_risk_raw = agent_risk_raw.upper()

    risk_level = RISK_MAP.get(agent_risk_raw, "medium")
    risk_score = RISK_SCORE_MAP.get(risk_level, 0.5)

    coverage_ratio = round(days_remain / lead_time, 2) if lead_time else 0

    trend_raw = forecast.get("trend_direction", "stable").lower()
    trend = (
        "up"   if "up"   in trend_raw or "increas" in trend_raw else
        "down" if "down" in trend_raw or "decreas" in trend_raw else
        "stable"
    )
    raw_cat = str(pi.get("category") or "").strip()
    item = {
        "id":               f"inv-{sku}",
        "sku":              sku,
        "name":             str(pi.get("name", "") or sku).strip() or str(sku),
        
        "category":         raw_cat if raw_cat and raw_cat.lower() not in {"unknown", "nan", "none", ""} else "General",
        "stock":            round(current_stock),
        "stockMin":         stock.get("stock_min") or metrics.get("reorder_point", 0),
        "stockMax":         stock.get("stock_max") or (metrics.get("reorder_point", 0) * 2),
        "demandForecast24h": round(avg_daily),
        # Provenance de la prévision de demande (pipeline demand sensing) :
        # "demand_sensing_db" = baseline MSTL + correction XGBoost lue en DB,
        # "live_ts_engine" = TS engine calculé à la volée, "fallback_flat" sinon.
        "forecastSource":   forecast.get("forecast_source", "unknown"),
        "forecastEngine":   forecast.get("forecast_engine"),
        "coverageRatio":    coverage_ratio,
        "riskLevel":        risk_level,
        "riskScore":        risk_score,
        "riskRationale":    risk.get("rationale", ""),
        "trend":            trend,
        "confidence":       0.85 if report.get("reasoning_source") == "llm" else 0.60,
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
                                report.get("analyst_flag", ""),
                            ])),
        "riskOverride":     risk.get("risk_override"),
        "objectiveConflict": report.get("objective_conflict", False),
        "unitCost":         pi.get("unit_cost", 0),
        "moq":              pi.get("moq", 0),
        # ── Decision agent output ────────────────────────────────────────
        # decision_result is always present when the full pipeline ran.
        # dec falls back to {} so every .get() below is safe.
        "recommendation":         None,
        "recommendationDetail":   None,
        "recommendationId":       None,   # inventory.recommendations UUID — used by PATCH /recommendations/{id}
        "recommendationStatus":   "pending",  # current DB status — used by frontend to rehydrate UI state
        "finalOrderQty":          None,
        "orderTiming":            None,
        "decisionConfidence":     None,
        "escalateToHuman":        False,
        "escalationReason":       None,
        "tradeOffs":              None,
    }

    # Patch in decision agent fields after item dict is built.
    # Kept separate so the block is easy to find and the fallback Nones above
    # remain valid when decision_result is missing (fast/rule-based path).
    decision_result = result.get("decision_result", {}) or {}
    dec             = decision_result.get("decision", {}) or {}

    if dec:
        item["recommendation"]       = dec.get("action")
        item["recommendationDetail"] = dec.get("recommendation_text")
        item["recommendationId"]     = decision_result.get("recommendation_id")
        item["finalOrderQty"]        = dec.get("order_qty")
        item["orderTiming"]          = dec.get("urgency")
        item["decisionConfidence"]   = dec.get("confidence")
        item["escalateToHuman"]      = bool(dec.get("escalate_to_human", False))
        item["escalationReason"]     = dec.get("escalation_reason")
        item["tradeOffs"]            = dec.get("trade_offs")

        # Pull the live status from DB so the frontend can rehydrate UI state.
        # get_latest_recommendation returns the most recent row for this SKU.
        rec_id = decision_result.get("recommendation_id")
        if rec_id:
            try:
                from app.inventory.repositories.inventory_repo import SyncInventoryRepo
                rec_row = SyncInventoryRepo.get_recommendation_by_id(rec_id)
                if rec_row:
                    item["recommendationStatus"] = rec_row.get("status", "pending")
            except Exception:
                pass  # Non-fatal — frontend defaults to pending

    return item


# Grille du juge inventaire (français) → axes attendus par le radar frontend.
# Voir evals/judge.py::INVENTORY_CRITERIA et le mockup "Jugement IA".
_JUDGE_AXIS_MAP = {
    "clarte":        "clarity",
    "coherence":     "coherence",
    "completude":    "completeness",
    "actionabilite": "actionability",
    "richesse":      "richness",
    "ancrage":       "grounding",
}


def _attach_inventory_judge(result: Dict[str, Any], item: Dict[str, Any]) -> None:
    """Fait noter le recommendation_text du DecisionAgent par le LLM-as-judge
    (evals.judge.judge_inventory_answer) et attache les 6 scores (0-5) à
    item['judge'] pour alimenter le radar "Jugement IA" du frontend.

    Best-effort : réservé au deep-dive single-SKU (POST /analyze) — jamais au
    batch, un appel LLM de juge par SKU y serait prohibitif. Toute erreur ou
    indisponibilité laisse item['judge'] = None → le radar reste masqué, aucune
    valeur inventée.
    """
    item["judge"] = None
    dec = (result.get("decision_result") or {}).get("decision") or {}
    rec_text = str(dec.get("recommendation_text") or "").strip()
    if not rec_text:
        return
    try:
        import json as _json
        from evals.judge import judge_inventory_answer

        analysis  = result.get("analysis_report") or {}
        context_r = result.get("context_result") or {}
        adjusted  = (result.get("decision_result") or {}).get("adjusted_metrics") or {}

        scenario = (
            f"{item.get('name')} ({item.get('sku')}) — stock {item.get('stock')} u, "
            f"risque {item.get('riskLevel')}, couverture {item.get('daysOfStock')} j, "
            f"délai fournisseur {item.get('leadTimeDays')} j, action recommandée "
            f"{dec.get('action')}."
        )
        context = "\n\n".join([
            "baseline_report: "  + _json.dumps(analysis,  ensure_ascii=False, default=str),
            "context_report: "   + _json.dumps(context_r, ensure_ascii=False, default=str),
            "adjusted_metrics: " + _json.dumps(adjusted,  ensure_ascii=False, default=str),
        ])

        # max_retries=1 : un seul réessai pour absorber un 429 « per-minute »
        # transitoire. Inutile d'insister davantage : quand le quota QUOTIDIEN
        # free-tier est épuisé (cycles de fond), aucun réessai ne passera —
        # l'endpoint est non bloquant, on abandonne vite et le radar reste masqué.
        j = judge_inventory_answer(scenario, rec_text, context=context, max_retries=1)
        if not j.ok or not j.scores:
            logger.info("[judge] juge indisponible SKU=%s: %s", item.get("sku"), j.error)
            return

        axes = {
            en: int(j.scores[fr])
            for fr, en in _JUDGE_AXIS_MAP.items()
            if isinstance(j.scores.get(fr), (int, float))
        }
        if len(axes) < len(_JUDGE_AXIS_MAP):
            return

        item["judge"] = {
            **axes,
            "overall":     round(sum(axes.values()) / len(axes), 2),
            "verdict":     j.verdict,
            "judge_model": j.judge_model,
        }
    except Exception:
        logger.warning("[analyze] LLM judge en échec SKU=%s", item.get("sku"), exc_info=True)
        item["judge"] = None


def _build_alerts(items: List[Dict[str, Any]], store_id: str = None) -> List[Dict[str, Any]]:
    """
    Build alert list from analyzed items.

    alert_type values written to DB must satisfy the alerts_alert_type_check
    constraint.  The DB check constraint uses: 'stockout_risk', 'below_minimum',
    'overstock'.  The frontend receives these mapped back to its own vocabulary
    ('rupture', 'redistribution', 'overstock') by get_store_alerts().

    If store_id is provided, all alerts are upserted to inventory.alerts in a single
    batched DB call (one connection, one transaction) so the frontend PATCH call
    gets real UUIDs.  Falls back to fake ids if the DB is unavailable.

    Alerts whose DB row is already in a terminal status (validated/rejected/
    dismissed/resolved) within the 24-hour cooldown are suppressed from the
    returned list so they don't reappear as ghost alerts after being actioned.
    """
    # ── Classify every actionable item ───────────────────────────────────────
    candidates: List[Dict[str, Any]] = []
    at_risk_skus: set = set()
    for item in items:
        if item.get("error"):
            continue

        risk  = item["riskLevel"]
        stock = item["stock"]
        name  = item["name"]
        days  = item["daysOfStock"]
        lt    = item["leadTimeDays"]
        sku   = item["sku"]

        alert_type = None
        urgency    = None
        title      = None
        message    = None

        if risk == "critical":
            alert_type = "stockout_risk"   # DB constraint value
            urgency    = "critical"
            title      = f"Rupture imminente : {name}"
            message    = (
                f"{name} — plus que {stock:.0f} unité(s) en stock, "
                f"{days:.1f} jour(s) de stock restant"
            )
        elif risk == "high":
            alert_type = "below_minimum"   # DB constraint value
            urgency    = "high"
            title      = f"Stock faible : {name}"
            message    = (
                f"{name} — {days:.1f}j de stock dans la fenêtre de variabilité "
                f"du délai fournisseur ({lt:.0f}j en moyenne)"
            )
        elif item.get("overstockFlag"):
            alert_type = "overstock"       # DB constraint value
            urgency    = "medium"
            title      = f"Surstock : {name}"
            message    = (
                f"{name} — {stock:.0f} unités en stock dépassent la fourchette normale. "
                f"{days:.1f}j de couverture, envisager une redistribution ou une promotion."
            )

        if not alert_type:
            continue

        at_risk_skus.add(sku)
        candidates.append({
            "sku":                sku,
            "store_id":           store_id,
            "alert_type":         alert_type,
            "severity":           urgency,
            "recommended_action": message,
            # carry display fields through
            "_title":  title,
            "_time":   item["lastUpdated"],
            "_days":   days,   # used for sort: highs ordered by least runway first
        })

    if not candidates:
        if store_id:
            try:
                from app.inventory.repositories.inventory_repo import SyncInventoryRepo
                SyncInventoryRepo.resolve_stale_alerts(store_id, at_risk_skus)
            except Exception as exc:
                logger.warning("_build_alerts: resolve_stale_alerts failed for %s: %s", store_id, exc)
        return []

    # ── Cap alerts to the most actionable ones ────────────────────────────────
    # Keep ALL criticals (stockout_risk) — those always need attention.
    # Fill remaining slots with high-risk (below_minimum) ordered by days-of-stock
    # ascending (least runway first).  Overstock alerts come last and are capped
    # only if total would exceed MAX_ALERTS.
    MAX_ALERTS = 50
    criticals  = [c for c in candidates if c["alert_type"] == "stockout_risk"]
    highs      = sorted(
        [c for c in candidates if c["alert_type"] == "below_minimum"],
        key=lambda c: c.get("_days", 999),
    )
    overstocks = [c for c in candidates if c["alert_type"] == "overstock"]

    remaining  = max(0, MAX_ALERTS - len(criticals))
    candidates = criticals + highs[:remaining]
    # Add overstock only if still have room
    remaining  = max(0, MAX_ALERTS - len(candidates))
    candidates = candidates + overstocks[:remaining]

    if not candidates:
        return []

    # ── Single batched upsert — one connection for all alerts ─────────────────
    id_map:       Dict[str, str] = {}
    actioned_ids: set            = set()
    if store_id:
        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo
            id_map = SyncInventoryRepo.upsert_alerts_batch(candidates)
            # Find which returned UUIDs are already in a terminal state so we
            # can suppress them — operator actioned them, no need to show again.
            if id_map:
                actioned_ids = SyncInventoryRepo.get_non_pending_alert_ids(
                    set(id_map.values())
                )
        except Exception as exc:
            logger.warning("_build_alerts: batch upsert failed for %s: %s", store_id, exc)

        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo
            SyncInventoryRepo.resolve_stale_alerts(store_id, at_risk_skus)
        except Exception as exc:
            logger.warning("_build_alerts: resolve_stale_alerts failed for %s: %s", store_id, exc)

    # ── Assemble final alert list — skip already-actioned ones ────────────────
    alerts = []
    for c in candidates:
        key    = f"{c['sku']}:{store_id}:{c['alert_type']}"
        db_id  = id_map.get(key)

        # Suppress alerts the operator already handled within the 24-hour window
        if db_id and db_id in actioned_ids:
            logger.debug(
                "_build_alerts: suppressing actioned alert %s (%s @ %s)",
                db_id, c["sku"], c["alert_type"],
            )
            continue

        # Fake id uses the DB alert_type value so it's clearly synthetic
        fake_id = f"alert-{c['alert_type']}-{c['sku']}"
        alerts.append({
            "id":      db_id or fake_id,
            "sku":     c["sku"],
            "type":    c["alert_type"],
            "urgency": c["severity"],
            "title":   c["_title"],
            "message": c["recommended_action"],
            "action":  None,
            "time":    c["_time"],
            "fromDb":  bool(db_id),
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

@router.post("/chat")
async def inventory_chat(request: Request, body: dict) -> Dict[str, Any]:
    """
    Chat Stock — jusqu'ici 404 (aucune route ne backait
    POST /api/inventory/chat, cf. post-mortem « Coach Chat — Agent Stock vs
    Ventes »). Le mode Ventes répond déjà correctement aux questions stock et
    cross-domaine car /api/v1/coach/chat charge systématiquement le contexte
    inventaire + RAG stock, quel que soit le domaine détecté. On délègue donc
    à EXACTEMENT la même logique plutôt que d'en réécrire une nouvelle, et on
    reforme juste la réponse au contrat attendu par le frontend
    (InventoryChatResponse : answer/intent/sku/data_source/timestamp).

    Limite connue : pas de verrouillage de SKU multi-tour côté serveur — `sku`
    ci-dessous n'est que l'écho du sku_context envoyé par le frontend, pas une
    résolution réelle depuis le message. À construire plus tard si besoin
    (matching nom produit → SKU via le catalogue + persistance par tour).
    """
    import json as _json
    from datetime import datetime as _dt
    from app.sales.coaching.agents.coach.coach_chat import coach_chat as _sales_coach_chat

    message     = (body.get("message") or "").strip()
    store_id    = body.get("store_id") or DEFAULT_STORE_ID
    sku_context = body.get("sku_context")

    coach_body = {
        "message":      message,
        "advisor_name": "Conseiller",
        "store_id":     store_id,
        "context":      {"domain": "stock"},
    }

    try:
        resp = await _sales_coach_chat(request, coach_body)
        data = _json.loads(resp.body)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[INVENTORY CHAT] delegation to coach failed: %s", exc)
        raise HTTPException(status_code=502, detail="Coach backend unavailable")

    return {
        "answer":      data.get("reply", ""),
        "intent":      data.get("question_type") or data.get("mode") or "free_question",
        "sku":         sku_context,
        "data_source": "cache" if data.get("source") == "cache" else "fresh_analysis",
        "timestamp":   _dt.now().isoformat(),
    }


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
    item = _to_inventory_item(result, preloaded_stock=None, product_lookup=None)
    # NB : le LLM-as-judge n'est PLUS appelé ici (il bloquerait l'ouverture de
    # la modale). Le frontend appelle POST /judge juste après, en repassant ce
    # `raw` + `item`, pour remplir le radar sans latence sur /analyze.
    return {
        "raw":  result,
        "item": item,
    }


@router.post("/judge")
def judge_single(req: JudgeRequest) -> Dict[str, Any]:
    """Note qualité (6 axes 0-5) de la reco produite par /analyze.

    Appel séparé et non bloquant : le frontend l'invoque après avoir affiché la
    recommandation, avec le `raw` + `item` déjà reçus. Aucun rejeu du pipeline —
    juste l'appel LLM du juge. Best-effort : renvoie {"judge": null} si le juge
    est indisponible."""
    item = dict(req.item)
    _attach_inventory_judge(req.raw, item)
    return {"judge": item.get("judge")}


def analyze_store(
    store_id: str,
    business_objective: str = "balanced",
    force_refresh: bool = False,
    fast: bool = True,
    page: int = 1,
    page_size: int = 100,
    blocking: bool = True,
) -> Dict[str, Any]:
    """
    Full store analysis with result caching and pagination.

    With 100+ SKUs the pipeline can take minutes on first call (see
    DEMO_SKU_CAP / orchestrator comments for why). Subsequent calls within
    CACHE_TTL are instant. force_refresh=True always re-runs the pipeline.
    fast=True uses the rule-based orchestrator (no LLM) for WS broadcasts.

    blocking controls what happens when another caller is already running
    the pipeline for this store+objective:
      - blocking=True  (trusted background callers: prewarm, WS push,
        summary endpoint) waits for the in-flight run and reuses its result.
        This preserves the original behavior for callers that are already
        off the request/response path.
      - blocking=False (the public HTTP route) never waits — it returns
        immediately with the last cached payload (marked "stale"/"computing")
        or a minimal "computing" placeholder if there's no cache yet at all.
        This is what stops a pile of concurrent frontend polls from each
        parking a worker thread for the full multi-minute pipeline run,
        which is what was starving unrelated requests (openapi.json, WS
        handshakes) under load.

    Pagination:
      GET /store/I63?page=1&page_size=100   → first 100 SKUs
      GET /store/I63?page=2&page_size=100   → next 100
      GET /store/I63?page_size=0            → all (slow — avoid on large stores)
    """
    # Direct Python calls (WS broadcast, get_summary) bypass FastAPI's
    # dependency injection, so Query(...) sentinels leak through as defaults.
    from fastapi.params import Query as _QueryParam
    if isinstance(business_objective, _QueryParam):
        business_objective = business_objective.default
    if isinstance(force_refresh, _QueryParam):
        force_refresh = force_refresh.default
    if isinstance(page, _QueryParam):
        page = page.default
    if isinstance(page_size, _QueryParam):
        page_size = page_size.default
    # Check cache first (no lock needed for read)
    if not force_refresh:
        cached = _get_cached(store_id, business_objective)
        if cached is not None:
            return _paginate(cached, page, page_size)

    # Acquire per-store lock so only ONE pipeline runs at a time.
    lock = _get_store_lock(f"{store_id}::{business_objective}")

    if blocking:
        lock.acquire()
    else:
        acquired = lock.acquire(blocking=False)
        if not acquired:
            # Someone else (cron cycle, WS push, another HTTP poll) is already
            # running this pipeline. Don't block a worker thread waiting on
            # it — hand back whatever we have and let the frontend re-poll.
            stale_entry = _store_cache.get(_cache_key(store_id, business_objective))
            if stale_entry is not None:
                payload = dict(stale_entry["data"])
                payload["status"] = "computing"
                payload["stale"] = True
                payload["stale_age_seconds"] = int(time.time() - stale_entry["ts"])
                logger.info(
                    "Pipeline busy for %s — returning stale cache (age %ds)",
                    store_id, payload["stale_age_seconds"],
                )
                return _paginate(payload, page, page_size)
            logger.info("Pipeline busy for %s — no cache yet, returning placeholder", store_id)
            return {
                "store_id":           store_id,
                "business_objective": business_objective,
                "status":             "computing",
                "total_skus":         0,
                "items":              [],
                "alerts":             [],
                "summary":            {},
                "message":            "First analysis in progress — retry in a few seconds.",
            }

    try:
        # Re-check cache now that we hold the lock — another thread may have populated it
        cached = _get_cached(store_id, business_objective)
        if cached is not None and not force_refresh:
            logger.info("Cache populated by concurrent run for %s — reusing", store_id)
            return _paginate(cached, page, page_size)

        skus = _resolve_skus_for_store(store_id)

        orchestrator = get_orchestrator_fast() if fast else get_orchestrator()
        _pipeline_t0 = time.time()
        logger.info(
            "Running pipeline for %s (%d SKUs) [fast=%s, objective=%s]...",
            store_id, len(skus), fast, business_objective,
        )
        results = orchestrator.analyze_batch(skus, store_id, business_objective)
        _pipeline_ms = int((time.time() - _pipeline_t0) * 1000)
        logger.info(
            "Pipeline finished for %s in %dms (%d SKUs, %.1fms/SKU avg)",
            store_id, _pipeline_ms, len(skus),
            _pipeline_ms / max(len(skus), 1),
        )
        results = _enrich_with_product_master(results)

        # ── Pre-fetch stock for all SKUs in ONE DB query ──────────────────
        # _to_inventory_item() was calling SyncInventoryRepo.get_stock_level()
        # per SKU — 110 DB connections opened/closed per pipeline run.
        # We fetch all stock levels here in a single query and pass a lookup
        # dict into _to_inventory_item so it never touches the DB.
        preloaded_stock: Dict[str, int] = {}
        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo as _Repo
            batch = _Repo.get_stock_levels_batch(skus, store_id)
            if batch:
                preloaded_stock = batch
                logger.info("Pre-fetched stock for %d/%d SKUs", len(batch), len(skus))
        except Exception as exc:
            logger.warning("Stock batch pre-fetch failed (%s) — falling back to per-SKU DB reads", exc)

        # ── Pre-build product lookup dict from cached CSV ─────────────────
        # _to_inventory_item() was filtering _DataCache.product() per SKU
        # inside a loop — 110 DataFrame filter operations.
        # Build the dict once here from the already-loaded CSV (instant).
        try:
            _pm = _DataCache.product().copy()
            _pm["sku"] = _pm["sku"].astype(str)
            product_lookup: Dict[str, Any] = {
                row["sku"]: row.to_dict()
                for _, row in _pm.iterrows()
            }
        except Exception:
            product_lookup = {}

        items = [_to_inventory_item(r, preloaded_stock=preloaded_stock, product_lookup=product_lookup) for r in results]
        alerts  = _build_alerts(items, store_id=store_id)
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
    finally:
        lock.release()


@router.get("/store/{store_id}")
def get_store_inventory(
    store_id: str,
    business_objective: str = Query(default="balanced"),
    force_refresh: bool = Query(default=False),
    fast: bool = Query(default=True),
    # ── Pagination ────────────────────────────────────────────────────────
    # page     : 1-based page index
    # page_size: items per page (0 = return all, use with care on 4 000 SKUs)
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=0, le=500),
) -> Dict[str, Any]:
    """
    HTTP entrypoint for store inventory. Thin wrapper around analyze_store()
    with blocking=False: if a pipeline run is already in flight for this
    store+objective (cron cycle, WS push, another poll), this returns
    immediately with the last cached payload (flagged "stale"/"computing")
    instead of parking a worker thread for the multi-minute pipeline run.
    Poll again in a few seconds — check `status` in the response.
    """
    return analyze_store(
        store_id, business_objective, force_refresh=force_refresh,
        fast=fast, page=page, page_size=page_size, blocking=False,
    )


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


@router.get("/store/{store_id}/critical-trend")
def get_critical_trend(
    store_id: str,
    hours_back: int = Query(default=48, ge=1, le=24 * 14),
) -> Dict[str, Any]:
    """
    Évolution du % de produits critiques sur les `hours_back` dernières
    heures — alimente le mini chart "Tendance du risque" du dashboard.

    Lit uniquement inventory.critical_trend_history (peuplée en tâche de
    fond, une ligne/heure/magasin, cf. critical_trend_snapshot.py). Ne
    déclenche jamais de recalcul du pipeline : si aucun snapshot n'existe
    encore (service démarré depuis moins d'une heure), `data` est vide et
    le frontend affiche l'état "en attente de données".
    """
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    rows = SyncInventoryRepo.get_critical_trend_history(store_id, hours_back)
    return {
        "store_id":   store_id,
        "hours_back": hours_back,
        "data": [
            {
                "hour":         r["snapshot_time"].isoformat(),
                "total_skus":   r["total_skus"],
                "critical_skus": r["critical_count"],
                "critical_pct": float(r["critical_pct"]),
            }
            for r in rows
        ],
    }


@router.get("/forecast/{store_id}/{sku}")
def get_demand_forecast(
    store_id: str,
    sku: str,
    days: int = Query(default=14, ge=1, le=30),
) -> Dict[str, Any]:
    """
    Série de prévision de demande jour par jour pour un SKU (pipeline demand
    sensing) : baseline MSTL 30 j + correction XGBoost 7 j quand elle existe.
    `predicted` = COALESCE(corrected, baseline) — la valeur que les agents
    utilisent réellement.
    """
    from app.inventory.tools.internal.stock_tools import get_forecast_data

    def _num(v):
        return None if v is None or pd.isna(v) else _json_safe(v)

    df = get_forecast_data(sku, store_id, days=days)
    points = []
    corrected_count = 0
    for row in df.itertuples(index=False):
        corrected = _num(getattr(row, "corrected_demand", None))
        if corrected is not None:
            corrected_count += 1
        points.append({
            "date":      row.date.date().isoformat() if hasattr(row.date, "date") else str(row.date),
            "predicted": _num(row.predicted_demand),
            "baseline":  _num(getattr(row, "baseline_demand", None)),
            "corrected": corrected,
        })
    return {
        "sku":            sku,
        "store_id":       store_id,
        "points":         points,
        "correctedDays":  corrected_count,
        "model":          "baseline_mstl_v1 + sensing_model_v1" if corrected_count else "baseline_mstl_v1",
        "source":         "inventory.demand_forecast",
    }


@router.delete("/cache")
def clear_cache() -> Dict[str, Any]:
    global _db_sku_cache
    count = len(_store_cache)
    _store_cache.clear()
    _DataCache.invalidate()
    _db_sku_cache = None   # force re-query inventory.products on next resolve
    logger.info("Cache cleared (%d store entries + CSV + DB SKU cache)", count)
    return {
        "cleared": count,
        "message": "All caches cleared. Next request re-runs the pipeline.",
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
            await websocket.send_json(_json_safe({
                "type": "inventory_update",
                "store_id": store_id,
                "business_objective": business_objective,
                **cached,
            }))
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
                            # fast=True (was False): this is the automatic
                            # cold-cache warm-up for the dashboard, not an
                            # explicit request for full-LLM analysis. Now that
                            # get_orchestrator() is genuinely LLM-backed (see
                            # get_orchestrator() above), fast=False here would
                            # have silently turned every cold dashboard load
                            # into a 100+ SKU x 3-agent LLM run.
                            force_refresh=False, fast=True,
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


# ══════════════════════════════════════════════════════════════════════════════
# Business Objectives Management
# ══════════════════════════════════════════════════════════════════════════════

class SetObjectiveRequest(BaseModel):
    label: str = Field(..., description="Objective label (e.g., 'balanced', 'cost_savings', 'high_demand')")


@router.get("/objectives")
async def list_objectives() -> Dict[str, Any]:
    """
    List all business objectives with their active status.
    Returns objectives ordered by priority.
    """
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo
        objectives = SyncInventoryRepo.list_objectives()
        return {"objectives": objectives, "count": len(objectives)}
    except Exception as exc:
        logger.error("Failed to list objectives: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/objectives/active")
async def set_active_objective(req: SetObjectiveRequest) -> Dict[str, Any]:
    """
    Set the active business objective.
    Invalidates all store caches so next analysis uses the new objective.
    """
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo
        success = SyncInventoryRepo.set_active_objective(req.label)
        if not success:
            raise HTTPException(status_code=404, detail=f"Objective '{req.label}' not found")
        _store_cache.clear()
        _DataCache.invalidate()
        logger.info("Active objective changed to '%s' — cache cleared", req.label)
        return {"active": req.label, "cache_cleared": True,
                "message": f"Switched to {req.label} mode."}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to set active objective: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# Alert Management
# ══════════════════════════════════════════════════════════════════════════════

class UpdateAlertRequest(BaseModel):
    status: str = Field(
        ...,
        description=(
            "New status. Allowed values: "
            "'acknowledged' — operator has seen it; "
            "'validated'    — operator confirmed and will act; "
            "'rejected'     — operator dismissed as false-positive; "
            "'resolved'     — issue is fixed; "
            "'dismissed'    — noise, no action needed."
        )
    )


@router.get("/alerts/{store_id}")
async def get_store_alerts(
    store_id: str,
    status: Optional[str] = Query(
        default="pending",
        description="Filter by status: 'pending', 'acknowledged', 'validated', "
                    "'resolved', 'dismissed', 'rejected'. Pass 'all' or omit to get all."
    )
) -> Dict[str, Any]:
    """
    Get alerts for a store from the database.

    By default returns only pending alerts.
    Pass ?status=all to get every alert regardless of status.
    The returned `id` field is the real DB UUID — use it with PATCH /alerts/{alert_id}.
    """
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo
        # Treat 'all' / empty string as "no filter"
        effective_status = None if status in ("all", "", None) else status
        alerts = SyncInventoryRepo.get_store_alerts(store_id, status=effective_status)
        # Datetime fields are already serialised to ISO strings by the repo,
        # but guard here in case the column type changes.
        for a in alerts:
            for col in ("triggered_at", "created_at", "resolved_at", "decided_at"):
                if a.get(col) and hasattr(a[col], "isoformat"):
                    a[col] = a[col].isoformat()
            # Normalize: frontend expects 'name', repo returns 'product_name'
            a['name'] = a.get('product_name') or a.get('sku', '')
            # Map DB alert_type to frontend type vocab
            a['type'] = {
                # current DB values (alerts_alert_type_check constraint)
                'stockout_risk':  'rupture',
                'below_minimum':  'redistribution',
                'overstock':      'overstock',
                # legacy values kept for any pre-existing rows in the DB
                'rupture':        'rupture',
                'redistribution': 'redistribution',
            }.get(a.get('alert_type', ''), a.get('alert_type', ''))
        return {
            "store_id": store_id,
            "alerts":   alerts,
            "count":    len(alerts),
            "filter":   effective_status or "all",
        }
    except Exception as exc:
        logger.error("Failed to get alerts for %s: %s", store_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/alerts/{alert_id}")
async def update_alert(alert_id: str, req: UpdateAlertRequest) -> Dict[str, Any]:
    """
    Update an alert's status.

    IMPORTANT: {alert_id} must be the UUID returned by GET /alerts/{store_id}
    in the 'id' field.  Fake IDs (alert-rupture-SKU123) will return 404.

    Valid statuses:
        acknowledged — operator has seen it (alert stays visible)
        validated    — operator confirmed and will act  → stamps resolved_at
        rejected     — false-positive, no action        → stamps resolved_at
        resolved     — issue is fixed                   → stamps resolved_at
        dismissed    — noise, no action needed          → stamps resolved_at
    """
    VALID_STATUSES = {"acknowledged", "validated", "rejected", "resolved", "dismissed"}

    if req.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{req.status}'. Must be one of: {sorted(VALID_STATUSES)}"
        )

    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo

        # Look up which store owns this alert so we can drop its cache entry.
        store_id_for_alert = SyncInventoryRepo.get_alert_store_id(alert_id)

        success = SyncInventoryRepo.update_alert_status(
            alert_id=alert_id,
            status=req.status,
        )
        if not success:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Alert '{alert_id}' not found. "
                    "Make sure you are using the UUID from GET /alerts/{store_id}, "
                    "not a client-generated fake id."
                )
            )
        logger.info("Alert %s -> %s", alert_id, req.status)

        # Drop the store cache so the next GET /store/{id} or WS push does not
        # serve stale alerts that still show the old status.
        if store_id_for_alert:
            keys_to_drop = [k for k in _store_cache if k.startswith(f"{store_id_for_alert}::")]
            for k in keys_to_drop:
                del _store_cache[k]
            if keys_to_drop:
                logger.info(
                    "Alert %s actioned → dropped %d cache entries for store %s",
                    alert_id, len(keys_to_drop), store_id_for_alert,
                )

        return {
            "alert_id": alert_id,
            "status":   req.status,
            "updated":  True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update alert %s: %s", alert_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# Recommendation Management
# ══════════════════════════════════════════════════════════════════════════════

class UpdateRecommendationRequest(BaseModel):
    status: str = Field(
        ...,
        description=(
            "New status. Allowed values: "
            "'approved' — operator confirmed the order; "
            "'rejected' — operator dismissed the recommendation."
        )
    )
    decided_by: Optional[str] = Field(
        default=None,
        description="Optional identifier for the user who made the decision.",
    )


@router.patch("/recommendations/{recommendation_id}")
async def update_recommendation(
    recommendation_id: str,
    req: UpdateRecommendationRequest,
) -> Dict[str, Any]:
    """
    Update a recommendation's status.

    {recommendation_id} must be the UUID returned in the 'recommendationId'
    field of each inventory item (written by the decision agent to inventory.recommendations).

    Valid statuses:
        approved — operator confirmed and will place the order
        rejected — operator dismissed the recommendation
    """
    VALID_STATUSES = {"approved", "rejected"}

    if req.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{req.status}'. Must be one of: {sorted(VALID_STATUSES)}",
        )

    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo

        success = SyncInventoryRepo.update_recommendation_status(
            recommendation_id=recommendation_id,
            status=req.status,
            decided_by=req.decided_by,
        )
        if not success:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Recommendation '{recommendation_id}' not found. "
                    "Make sure you are using the UUID from the inventory item's "
                    "'recommendationId' field."
                ),
            )

        logger.info(
            "Recommendation %s → %s (by %s)",
            recommendation_id, req.status, req.decided_by or "unknown",
        )

        # Boucle de feedback : la décision humaine (Approuver/Rejeter dans la
        # page Inventory) est journalisée dans public.agent_feedback puis
        # réinjectée dans les prompts DecisionAgent/Stratège par
        # feedback_service.get_learning_context_sync. Jamais bloquant.
        try:
            from app.core.feedback_service import record_feedback
            reco = SyncInventoryRepo.get_recommendation_by_id(recommendation_id) or {}
            record_feedback(
                store_id=str(reco.get("store_id") or "unknown"),
                source="reco",
                decision=req.status,                    # 'approved' | 'rejected'
                ref_id=recommendation_id,
                sku=int(reco["sku"]) if reco.get("sku") is not None else None,
                action_type=reco.get("recommendation_type") or "recommendation",
                payload={
                    "decided_by":   req.decided_by,
                    "order_qty":    reco.get("order_qty"),
                    "urgency":      reco.get("urgency"),
                },
            )
        except Exception as exc:
            logger.debug("Feedback recommendation %s non enregistré: %s", recommendation_id, exc)

        return {
            "recommendation_id": recommendation_id,
            "status":            req.status,
            "decided_by":        req.decided_by,
            "updated":           True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update recommendation %s: %s", recommendation_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# =====================================================================
# Demo helpers  (safe to call before a demo to pre-populate DB alerts)
# =====================================================================

@router.post("/alerts/sync/{store_id}")
def sync_alerts_to_db(store_id: str) -> Dict[str, Any]:
    """
    Force-write all current critical/high items from the cache into inventory.alerts.
    Call this once before the demo so every alert has a real UUID.
    Returns how many were written vs already existed.
    """
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    # Try all cached objectives for this store — take the first hit
    cached = None
    for key, entry in _store_cache.items():
        if key.startswith(f"{store_id}::"):
            cached = entry["data"]
            break
    if not cached:
        raise HTTPException(status_code=404, detail=f"No cached data for {store_id}. Load the inventory page first.")

    written  = 0
    existing = 0
    for item in cached.get("items", []):
        risk  = item.get("riskLevel", "ok")
        stock = item.get("stock", 0)
        sku   = item.get("sku", "")
        name  = item.get("name", sku)
        days  = item.get("daysOfStock", 0)
        lt    = item.get("leadTimeDays", 7)

        # Use DB constraint values (stockout_risk / below_minimum), NOT frontend vocab
        if risk == "critical":
            atype, severity = "stockout_risk", "critical"
            action = f"{name} : {stock} unité(s) en stock ({days:.1f}j restant(s)). Commander immédiatement."
        elif risk == "high":
            atype, severity = "below_minimum", "high"
            action = f"{name} : {days:.1f}j de stock vs {lt:.0f}j de délai fournisseur. Commander bientôt."
        else:
            continue

        db_id = SyncInventoryRepo.upsert_alert_and_return_id(
            sku=sku, store_id=store_id, alert_type=atype,
            severity=severity, recommended_action=action,
        )
        if db_id:
            written += 1
        else:
            existing += 1

    return {"written": written, "existing": existing, "total": written + existing,
            "message": f"DB now has {written+existing} pending alerts for {store_id}"}


@router.get("/debug/stock/{store_id}/{sku}")
def debug_stock(store_id: str, sku: str) -> Dict[str, Any]:
    """Quick check: what does DB say vs CSV for this SKU/store? Use before demo."""   
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    db_row = SyncInventoryRepo.get_stock_level(sku, store_id)
    try:
        stock_df = _DataCache.stock()
        csv_rows = stock_df[(stock_df["store_id"] == store_id) & (stock_df["sku"].astype(str) == sku)]
        csv_stock = int(csv_rows["stock_level"].iloc[-1]) if not csv_rows.empty else None
    except Exception as exc:
        csv_stock = f"error: {exc}"
    return {"sku": sku, "store_id": store_id,
            "db_stock_current":  db_row.get("stock_current") if db_row else None,
            "db_row_found":      db_row is not None,
            "csv_last_snapshot": csv_stock}