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

# Serveur MCP standalone : ajoute la racine du repo au path quand lancé en script
BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio
from typing import Any

# Data layer — reads only
from app.inventory.tools.internal.stock_tools import (
    get_stock_status,
    get_product,
    get_forecast,
    get_sales_history,
)
# Computation layer — math only
from app.inventory.agents.analysis.tools import (
    compute_inventory_metrics,
    compute_demand_std,
)
from app.inventory.config.settings import DEFAULT_STORE


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
            sku      = arguments.get("sku")
            store_id = arguments.get("store_id", DEFAULT_STORE)
            stock    = get_stock_status(sku, store_id)
            product  = get_product(sku)
            if not product or stock["source"] == "none":
                result = f"No data for SKU '{sku}' at store '{store_id}'."
            else:
                st = stock
                p  = product
                result = (
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

        elif name == "get_forecast_summary":
            sku         = arguments.get("sku")
            store_id    = arguments.get("store_id", DEFAULT_STORE)
            forecast_df = get_forecast(sku, store_id)
            if forecast_df.empty:
                result = f"No forecast data for SKU '{sku}' at store '{store_id}'."
            else:
                sales_df   = get_sales_history(sku, store_id)
                avg        = float(forecast_df["predicted_demand"].mean())
                total      = float(forecast_df["predicted_demand"].sum())
                peak       = float(forecast_df["predicted_demand"].max())
                low        = float(forecast_df["predicted_demand"].min())
                peak_dt    = forecast_df.loc[forecast_df["predicted_demand"].idxmax(), "date"].date()
                start      = forecast_df["date"].min().date()
                end        = forecast_df["date"].max().date()
                days       = len(forecast_df)
                demand_std = compute_demand_std(sales_df, avg)
                df_sorted  = forecast_df.sort_values("date")
                half       = days // 2
                fh_avg     = df_sorted.head(half)["predicted_demand"].mean()
                sh_avg     = df_sorted.tail(half)["predicted_demand"].mean()
                trend      = "up" if sh_avg > fh_avg * 1.05 else ("down" if sh_avg < fh_avg * 0.95 else "stable")
                df_sorted  = df_sorted.copy()
                df_sorted["week"] = (df_sorted["date"] - df_sorted["date"].min()).dt.days // 7 + 1
                weekly     = df_sorted.groupby("week")["predicted_demand"].sum().round(1)
                weekly_str = "  |  ".join([f"W{w}: {v:.0f}" for w, v in weekly.items()])
                result = (
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

        elif name == "compute_inventory_metrics":
            sku      = arguments.get("sku")
            store_id = arguments.get("store_id", DEFAULT_STORE)
            business_objective = arguments.get("business_objective", "balanced")
            promo_uplift_pct   = arguments.get("promo_uplift_pct", 0.0)
            stock       = get_stock_status(sku, store_id)
            product     = get_product(sku)
            forecast_df = get_forecast(sku, store_id)
            sales_df    = get_sales_history(sku, store_id)
            if not product or forecast_df.empty:
                result = f"No product/forecast data for SKU '{sku}'."
            else:
                avg_daily  = float(forecast_df["predicted_demand"].mean())
                total_30d  = float(forecast_df["predicted_demand"].sum())
                demand_std = compute_demand_std(sales_df, avg_daily)
                metrics = compute_inventory_metrics(
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
                m  = metrics["metrics"]
                r  = metrics["risk_assessment"]
                c  = metrics["constraints"]
                st = metrics["stock"]
                uplift_note = (
                    f"  Uplift applied            : +{promo_uplift_pct:.0f}% (Context Agent)\n"
                    if promo_uplift_pct > 0 else
                    "  Uplift applied            : none — baseline\n"
                )
                result = (
                    f"=== Inventory Metrics: {sku} @ {store_id} ===\n\n"
                    f"Current Status:\n"
                    f"  Stock level               : {st['current_stock']:.0f} units\n"
                    f"  Days of stock remaining   : {m['days_of_stock_remaining']:.1f} days\n"
                    f"  Risk level                : {r['level']}\n"
                    f"  Risk rationale            : {r['rationale']}\n"
                    f"  Overstock flag            : {'YES' if r['overstock_flag'] else 'No'}\n\n"
                    f"Demand:\n"
                    f"  Daily avg                 : {metrics['forecast']['avg_daily_demand']:.2f} units/day\n"
                    f"{uplift_note}"
                    f"  30d total                 : {metrics['forecast']['total_30d_demand']:.0f} units\n\n"
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
