"""
MCP-Backed LangChain Tools
===========================
Architecture note
-----------------
The MCP stdio transport (subprocess + pipes) deadlocks when called from
inside a ThreadPoolExecutor that is itself nested inside LangGraph's sync
executor.  Symptom: infinite hang, Ctrl+C ignored, only task-kill works.

Root cause: LangGraph.invoke() runs synchronously but internally uses an
event loop.  The original code detected a running loop → spawned a
ThreadPoolExecutor thread → called asyncio.run() inside it → spawned the
MCP subprocess with stdio pipes.  The subprocess's stdout is never read
because the parent thread is blocked on future.result(), creating a pipe
buffer deadlock.

Fix: for calls that originate WITHIN the inventory module, import and call
the Python functions directly.  No subprocess, no async, no deadlock.

MCP still exists for CROSS-MODULE calls (sales agent → inventory MCP server).
That path goes through _call_mcp_tool() and is only triggered externally.
"""

from langchain_core.tools import tool
from pathlib import Path
import sys
import asyncio
import concurrent.futures
import logging

logger = logging.getLogger(__name__)

# ── Path setup ────────────────────────────────────────────────────────────────
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from config.settings import DEFAULT_STORE

# ── Direct imports (no subprocess, no async) ──────────────────────────────────
# Data layer — reads only
from src.tools.internal.stock_tools import (
    get_stock_status  as _get_stock_status,
    get_product       as _get_product,
    get_forecast      as _get_forecast,
    get_sales_history as _get_sales_history,
)
# Computation layer — math only, lives in analysis/tools now
from src.agents.analysis.tools import (
    compute_inventory_metrics as _compute_inventory_metrics,
    compute_demand_std        as _compute_demand_std,
)

# ── MCP path (external/cross-module callers only) ─────────────────────────────
MCP_SERVER_PATH = str(
    Path(__file__).resolve().parent.parent.parent / "services" / "mcp_server.py"
)


# ---------------------------------------------------------------------------
# External MCP bridge  (sales module → inventory, not used internally)
# ---------------------------------------------------------------------------

def _run_async_safely(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result(timeout=60)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _call_mcp_tool_async(tool_name: str, arguments: dict) -> str:
    from src.integrations.mcp_client import InventoryMCPClient
    client = InventoryMCPClient(MCP_SERVER_PATH)
    async with client.connect():
        return await client.call_tool(tool_name, arguments)


def _call_mcp_tool(tool_name: str, arguments: dict) -> str:
    """External callers only. Do not call from inside the inventory module."""
    return _run_async_safely(_call_mcp_tool_async(tool_name, arguments))


# ---------------------------------------------------------------------------
# Batch helper for InventoryAnalysisAgent — pure in-process, no async
# ---------------------------------------------------------------------------

def batch_inventory_data(
    sku: str,
    store_id: str = DEFAULT_STORE,
    business_objective: str = "balanced",
    promo_uplift_pct: float = 0.0,
) -> dict:
    """
    Return stock, forecast, and computed metrics as a dict.
    Calls stock_tools + analysis/tools directly — no MCP, no subprocess, no async.
    This is the internal call path for the Analysis Agent.
    """
    logger.debug("batch_inventory_data: sku=%s store=%s obj=%s", sku, store_id, business_objective)

    stock       = _get_stock_status(sku, store_id)
    product     = _get_product(sku)
    forecast_df = _get_forecast(sku, store_id)
    sales_df    = _get_sales_history(sku, store_id)

    if not product or forecast_df.empty:
        return {"error": f"Missing product or forecast data for SKU {sku}", "sku": sku}

    avg_daily  = float(forecast_df["predicted_demand"].mean())
    total_30d  = float(forecast_df["predicted_demand"].sum())
    demand_std = _compute_demand_std(sales_df, avg_daily)

    metrics = _compute_inventory_metrics(
        stock_current        = stock["stock_current"],
        stock_in_transit     = stock["stock_in_transit"],
        stock_min            = stock["stock_min"],
        stock_max            = stock["stock_max"],
        lead_time_avg        = float(product["lead_time_days"]),
        lead_time_std        = float(product.get("lead_time_std", 0) or 0),
        moq                  = float(product["moq"]),
        unit_cost            = float(product["unit_cost"]),
        holding_cost_pct     = float(product.get("holding_cost_pct", 0.25) or 0.25),
        order_cost           = float(product.get("order_cost", 50.0) or 50.0),
        lifecycle_stage      = str(product.get("lifecycle_stage", "mature") or "mature"),
        service_level_target = float(product.get("service_level_target", 0.95) or 0.95),
        avg_daily_demand     = avg_daily,
        demand_std           = demand_std,
        total_30d_demand     = total_30d,
        trend_direction      = "stable",
        business_objective   = business_objective,
        promo_uplift_pct     = promo_uplift_pct,
    )

    return {
        "sku":               sku,
        "store_id":          store_id,
        "business_objective":business_objective,
        "stock":             stock,
        "product":           product,
        **metrics,
    }


# ---------------------------------------------------------------------------
# LangChain @tool wrappers — also call directly (no MCP subprocess)
# ---------------------------------------------------------------------------

@tool
def get_stock_status_mcp(sku: str, store_id: str = DEFAULT_STORE) -> str:
    """
    Get current stock status for a SKU.
    Returns stock level, lead time, MOQ, and unit costs.

    Args:
        sku: Product SKU code
        store_id: Store identifier (default: I63)
    """
    result = batch_inventory_data(sku, store_id)
    if "error" in result:
        return result["error"]
    st = result["stock"]
    p  = result["product"]
    return (
        f"=== Stock Status: {sku} @ {store_id} ===\n"
        f"  Product          : {p.get('product_name', 'N/A')}\n"
        f"  Category         : {p.get('category', 'N/A')}\n"
        f"  Lifecycle stage  : {p.get('lifecycle_stage', 'N/A')}\n"
        f"  Stock level      : {int(st['stock_current'])} units\n"
        f"  Stock in transit : {int(st['stock_in_transit'])} units\n"
        f"  Stockout flag    : {'YES' if st['stock_current'] == 0 else 'No'}\n"
        f"  Stock min        : {st['stock_min'] or 'not set'}\n"
        f"  Stock max        : {st['stock_max'] or 'not set'}\n"
        f"  Lead time avg    : {float(p.get('lead_time_days', 7)):.0f} days\n"
        f"  Lead time std    : {float(p.get('lead_time_std', 0) or 0):.1f} days\n"
        f"  MOQ              : {int(p.get('moq', 1) or 1)} units\n"
        f"  Unit cost        : {float(p.get('unit_cost', 0) or 0):.2f} DT\n"
        f"  Unit price       : {float(p.get('unit_price', 0) or 0):.2f} DT\n"
        f"  Holding cost pct : {float(p.get('holding_cost_pct', 0.25) or 0.25):.0%} / year\n"
        f"  Order cost       : {float(p.get('order_cost', 0) or 0):.2f} DT / order\n"
        f"  Service lvl tgt  : {float(p.get('service_level_target') or 0.95):.0%}\n"
        f"  Source           : {st['source']}"
    )


@tool
def get_forecast_summary_mcp(sku: str, store_id: str = DEFAULT_STORE) -> str:
    """
    Get 30-day demand forecast summary (TimesFM baseline, no promo adjustment).

    Args:
        sku: Product SKU code
        store_id: Store identifier (default: I63)
    """
    import math
    forecast_df = _get_forecast(sku, store_id)
    if forecast_df.empty:
        return f"No forecast data for SKU '{sku}' at store '{store_id}'."
    sales_df   = _get_sales_history(sku, store_id)
    avg        = float(forecast_df["predicted_demand"].mean())
    total      = float(forecast_df["predicted_demand"].sum())
    peak       = float(forecast_df["predicted_demand"].max())
    low        = float(forecast_df["predicted_demand"].min())
    peak_dt    = forecast_df.loc[forecast_df["predicted_demand"].idxmax(), "date"].date()
    start      = forecast_df["date"].min().date()
    end        = forecast_df["date"].max().date()
    days       = len(forecast_df)
    demand_std = _compute_demand_std(sales_df, avg)
    df_sorted  = forecast_df.sort_values("date")
    half       = days // 2
    fh_avg     = df_sorted.head(half)["predicted_demand"].mean()
    sh_avg     = df_sorted.tail(half)["predicted_demand"].mean()
    trend      = "up" if sh_avg > fh_avg * 1.05 else ("down" if sh_avg < fh_avg * 0.95 else "stable")
    df_sorted  = df_sorted.copy()
    df_sorted["week"] = (df_sorted["date"] - df_sorted["date"].min()).dt.days // 7 + 1
    weekly     = df_sorted.groupby("week")["predicted_demand"].sum().round(1)
    weekly_str = "  |  ".join([f"W{w}: {v:.0f}" for w, v in weekly.items()])
    return (
        f"=== Forecast (TimesFM Baseline): {sku} @ {store_id} ===\n"
        f"  Period         : {start} → {end} ({days} days)\n"
        f"  Total demand   : {total:.0f} units\n"
        f"  Daily avg      : {avg:.2f} units/day\n"
        f"  Daily std dev  : {demand_std:.2f} units/day\n"
        f"  Daily peak     : {peak:.2f} units on {peak_dt}\n"
        f"  Daily low      : {low:.2f} units/day\n"
        f"  Trend          : {trend}\n"
        f"  Weekly split   : {weekly_str}\n"
        f"  Note           : No promotional adjustment applied."
    )


@tool
def compute_inventory_metrics_mcp(
    sku: str,
    store_id: str = DEFAULT_STORE,
    promo_uplift_pct: float = 0.0,
    business_objective: str = "balanced",
) -> str:
    """
    Compute full inventory replenishment metrics.
    Returns risk level, safety stock, EOQ, ROP, recommended order quantity.

    Args:
        sku: Product SKU code
        store_id: Store identifier (default: I63)
        promo_uplift_pct: 0.0 for baseline (Context Agent sets this later)
        business_objective: cost | balanced | service_level | competitive
    """
    result = batch_inventory_data(sku, store_id, business_objective, promo_uplift_pct)
    if "error" in result:
        return result["error"]
    m  = result["metrics"]
    r  = result["risk_assessment"]
    c  = result["constraints"]
    st = result["stock"]
    uplift_note = (
        f"  Uplift applied            : +{promo_uplift_pct:.0f}% (Context Agent)\n"
        if promo_uplift_pct > 0 else
        "  Uplift applied            : none — baseline\n"
    )
    return (
        f"=== Inventory Metrics: {sku} @ {store_id} ===\n\n"
        f"Current Status:\n"
        f"  Stock level               : {st['current_stock']:.0f} units\n"
        f"  Days of stock remaining   : {m['days_of_stock_remaining']:.1f} days\n"
        f"  Risk level                : {r['level']}\n"
        f"  Risk rationale            : {r['rationale']}\n"
        f"  Overstock flag            : {'YES' if r['overstock_flag'] else 'No'}\n\n"
        f"Demand:\n"
        f"  Daily avg                 : {result['forecast']['avg_daily_demand']:.2f} units/day\n"
        f"{uplift_note}"
        f"  30d total                 : {result['forecast']['total_30d_demand']:.0f} units\n\n"
        f"Replenishment:\n"
        f"  Safety stock              : {m['safety_stock']:.0f} units\n"
        f"  Reorder point             : {m['reorder_point']:.0f} units\n"
        f"  EOQ                       : {m['eoq']:.0f} units\n"
        f"  MOQ                       : {c['moq']:.0f} units\n"
        f"  Formula order qty         : {m['formula_order_qty']:.0f} units\n"
        f"  Total replenishment cost  : {m['total_replenishment_cost']:,.0f} DT\n"
        f"  Business objective        : {business_objective}\n"
        f"  Lifecycle stage           : {st['lifecycle_stage']}"
    )


MCP_TOOLS = [
    get_stock_status_mcp,
    get_forecast_summary_mcp,
    compute_inventory_metrics_mcp,
]