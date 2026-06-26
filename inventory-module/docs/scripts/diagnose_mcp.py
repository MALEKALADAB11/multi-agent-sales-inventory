#!/usr/bin/env python3
"""
MCP End-to-End Diagnostic
==========================
Run this BEFORE main.py to confirm the full MCP chain works.

Usage:
    cd backend/
    python test_mcp_e2e.py

What it checks:
  1. Python path and imports
  2. Data files exist
  3. MCP server can be imported standalone
  4. MCP client can connect and call each tool
  5. LangChain @tool wrappers work (the actual path agents use)
"""

import sys
import asyncio
from pathlib import Path

# ── 0. Paths ──────────────────────────────────────────────────────────────────
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

MCP_SERVER_PATH = str(BACKEND_ROOT / "inventory" / "services" / "mcp_server.py")

print("=" * 60)
print("MCP END-TO-END DIAGNOSTIC")
print("=" * 60)

# ── 1. Imports ────────────────────────────────────────────────────────────────
print("\n[1/5] Checking imports...")
try:
    from config.settings import DEFAULT_STORE, STOCK_HISTORY_PATH, FORECAST_OUTPUT_PATH
    print(f"  ✓ config.settings  (DEFAULT_STORE={DEFAULT_STORE})")
except Exception as e:
    print(f"  ✗ config.settings FAILED: {e}")
    print("      → Check that config/__init__.py exists (double underscores)")
    sys.exit(1)

try:
    from src.tools.internal.stock_tools import (
        get_stock_status,
        get_forecast_summary,
        compute_inventory_metrics,
    )
    print("  ✓ stock_tools")
except Exception as e:
    print(f"  ✗ stock_tools FAILED: {e}")
    print("      → Check inventory/__init__.py and inventory/tools/__init__.py")
    sys.exit(1)

try:
    from src.integrations.mcp_client import InventoryMCPClient
    print("  ✓ mcp_client")
except Exception as e:
    print(f"  ✗ mcp_client FAILED: {e}")
    sys.exit(1)

# ── 2. Data files ─────────────────────────────────────────────────────────────
print("\n[2/5] Checking data files...")
for label, path in [
    ("stock_history", STOCK_HISTORY_PATH),
    ("forecast",      FORECAST_OUTPUT_PATH),
]:
    if Path(path).exists():
        print(f"  ✓ {label}: {path}")
    else:
        print(f"  ✗ MISSING: {path}")
        sys.exit(1)

# ── 3. Direct tool call (no MCP) ──────────────────────────────────────────────
print("\n[3/5] Direct stock_tools call (no MCP)...")
try:
    import pandas as pd
    df = pd.read_csv(STOCK_HISTORY_PATH)
    test_sku = df["sku"].iloc[0]
    result = get_stock_status(test_sku, DEFAULT_STORE)
    print(f"  ✓ get_stock_status({test_sku})")
    print("  " + result.split("\n")[0])
except Exception as e:
    print(f"  ✗ Direct call FAILED: {e}")
    sys.exit(1)

# ── 4. MCP client connection ──────────────────────────────────────────────────
print("\n[4/5] MCP client connection...")

if not Path(MCP_SERVER_PATH).exists():
    print(f"  ✗ MCP server script not found: {MCP_SERVER_PATH}")
    sys.exit(1)
print(f"  ✓ Server script found: {MCP_SERVER_PATH}")


async def test_mcp_client():
    client = InventoryMCPClient(MCP_SERVER_PATH)
    async with client.connect():
        tools = client.get_available_tools()
        print(f"  ✓ Connected. Available tools: {tools}")

        result = await client.call_tool(
            "get_stock_status", {"sku": test_sku, "store_id": DEFAULT_STORE}
        )
        print(f"  ✓ get_stock_status via MCP: OK ({len(result)} chars)")
        return True


try:
    asyncio.run(test_mcp_client())
except Exception as e:
    print(f"  ✗ MCP connection FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ── 5. LangChain @tool wrappers ───────────────────────────────────────────────
print("\n[5/5] LangChain @tool wrappers (the path agents actually use)...")
try:
    from src.tools.internal.mcp_wrappers import (
        get_stock_status_mcp,
        get_forecast_summary_mcp,
        compute_inventory_metrics_mcp,
    )

    r1 = get_stock_status_mcp.invoke({"sku": test_sku, "store_id": DEFAULT_STORE})
    print(f"  ✓ get_stock_status_mcp: OK ({len(r1)} chars)")

    r2 = get_forecast_summary_mcp.invoke({"sku": test_sku, "store_id": DEFAULT_STORE})
    print(f"  ✓ get_forecast_summary_mcp: OK ({len(r2)} chars)")

    r3 = compute_inventory_metrics_mcp.invoke({
        "sku": test_sku,
        "store_id": DEFAULT_STORE,
        "business_objective": "balanced",
    })
    print(f"  ✓ compute_inventory_metrics_mcp: OK ({len(r3)} chars)")

except Exception as e:
    print(f"  ✗ @tool wrapper FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("ALL CHECKS PASSED ✓  —  Safe to run main.py")
print("=" * 60)
