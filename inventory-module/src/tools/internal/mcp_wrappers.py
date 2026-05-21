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
from src.tools.internal.stock_tools import (
    get_stock_status          as _get_stock_status,
    get_forecast_summary      as _get_forecast_summary,
    compute_inventory_metrics as _compute_inventory_metrics,
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
) -> dict:
    """
    Return all three tool outputs as a dict.
    Calls stock_tools functions directly — no MCP, no subprocess, no async.
    This is the internal call path for the Analysis Agent.
    """
    logger.debug("batch_inventory_data: sku=%s store=%s obj=%s", sku, store_id, business_objective)
    return {
        "stock_status": _get_stock_status(sku, store_id),
        "forecast":     _get_forecast_summary(sku, store_id),
        "metrics":      _compute_inventory_metrics(
                            sku, store_id,
                            promo_uplift_pct=0.0,
                            business_objective=business_objective,
                        ),
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
    return _get_stock_status(sku, store_id)


@tool
def get_forecast_summary_mcp(sku: str, store_id: str = DEFAULT_STORE) -> str:
    """
    Get 30-day demand forecast summary (TimesFM baseline, no promo adjustment).

    Args:
        sku: Product SKU code
        store_id: Store identifier (default: I63)
    """
    return _get_forecast_summary(sku, store_id)


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
    return _compute_inventory_metrics(
        sku, store_id,
        promo_uplift_pct=promo_uplift_pct,
        business_objective=business_objective,
    )


MCP_TOOLS = [
    get_stock_status_mcp,
    get_forecast_summary_mcp,
    compute_inventory_metrics_mcp,
]