"""
Inventory MCP Server
====================
MCP server exposing inventory analysis tools.

This server provides tools for:
- Stock status queries
- Demand forecasting
- Risk metrics computation
"""

import asyncio
import sys
import os
from pathlib import Path

# Add backend root to path - must be first
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(str(BACKEND_ROOT))

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio
from typing import Any

from src.tools.internal.stock_tools import (
    get_stock_status,
    get_forecast_summary,
    compute_inventory_metrics
)
from config.settings import DEFAULT_STORE


# Create MCP server
server = Server("inventory-advisor")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available inventory tools"""
    return [
        Tool(
            name="get_stock_status",
            description="Get current stock status for a SKU including stock level, lead time, MOQ, and costs",
            inputSchema={
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "description": "Product SKU code"
                    },
                    "store_id": {
                        "type": "string",
                        "description": "Store identifier",
                        "default": DEFAULT_STORE
                    }
                },
                "required": ["sku"]
            }
        ),
        Tool(
            name="get_forecast_summary",
            description="Get 30-day demand forecast summary with weekly breakdown",
            inputSchema={
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "description": "Product SKU code"
                    },
                    "store_id": {
                        "type": "string",
                        "description": "Store identifier",
                        "default": DEFAULT_STORE
                    }
                },
                "required": ["sku"]
            }
        ),
        Tool(
            name="compute_inventory_metrics",
            description="Compute full inventory replenishment metrics including risk assessment, order quantity, and costs",
            inputSchema={
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "description": "Product SKU code"
                    },
                    "store_id": {
                        "type": "string",
                        "description": "Store identifier",
                        "default": DEFAULT_STORE
                    },
                    "promo_uplift_pct": {
                        "type": "number",
                        "description": "Demand uplift percentage from promotions/events",
                        "default": 0.0
                    },
                    "business_objective": {
                        "type": "string",
                        "description": "Business objective: cost, balanced, service_level, or competitive",
                        "enum": ["cost", "balanced", "service_level", "competitive"],
                        "default": "balanced"
                    }
                },
                "required": ["sku"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Execute inventory tool"""
    
    try:
        if name == "get_stock_status":
            result = get_stock_status(
                sku=arguments.get("sku"),
                store_id=arguments.get("store_id", DEFAULT_STORE)
            )
        
        elif name == "get_forecast_summary":
            result = get_forecast_summary(
                sku=arguments.get("sku"),
                store_id=arguments.get("store_id", DEFAULT_STORE)
            )
        
        elif name == "compute_inventory_metrics":
            result = compute_inventory_metrics(
                sku=arguments.get("sku"),
                store_id=arguments.get("store_id", DEFAULT_STORE),
                promo_uplift_pct=arguments.get("promo_uplift_pct", 0.0),
                business_objective=arguments.get("business_objective", "balanced")
            )
        
        else:
            return [TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]
        
        return [TextContent(
            type="text",
            text=result
        )]
    
    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error executing {name}: {str(e)}"
        )]


async def main():
    """Run the MCP server"""
    import sys
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )
    except Exception as e:
        # Write error to stderr so client can see it
        print(f"MCP Server Error: {e}", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import traceback
        import sys
        traceback.print_exc(file=sys.stderr)
