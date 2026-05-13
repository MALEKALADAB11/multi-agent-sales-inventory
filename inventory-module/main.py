"""
Main Entry Point
================
Interactive CLI for running the inventory analysis pipeline.

Usage examples:
    # Single SKU
    python main.py --sku ACC-BUD-001

    # Single SKU, specific store and objective
    python main.py --sku ACC-BUD-001 --store STORE-002 --objective service_level

    # All SKUs in one store
    python main.py --store I63

    # All SKUs across all stores
    python main.py --all

    # List available SKUs and stores
    python main.py --list

    # Interactive mode (prompts you to choose)
    python main.py
"""

import sys
import argparse
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from src.services.orchestrator import create_orchestrator
from config.settings import STOCK_HISTORY_PATH, PRODUCT_MASTER_PATH

OBJECTIVES = ["balanced", "cost", "service_level", "competitive"]


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def get_all_skus() -> list:
    return sorted(pd.read_csv(STOCK_HISTORY_PATH)["sku"].unique().tolist())


def get_all_stores() -> list:
    return sorted(pd.read_csv(STOCK_HISTORY_PATH)["store_id"].unique().tolist())


def get_skus_for_store(store_id: str) -> list:
    df = pd.read_csv(STOCK_HISTORY_PATH)
    return sorted(df[df["store_id"] == store_id]["sku"].unique().tolist())


def list_available():
    skus   = get_all_skus()
    stores = get_all_stores()

    print("\nAvailable SKUs:")
    for s in skus:
        print(f"  {s}")

    print(f"\nAvailable Stores:")
    for s in stores:
        print(f"  {s}")

    print(f"\nAvailable Objectives:")
    for o in OBJECTIVES:
        print(f"  {o}")
    print()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _pct(val, default="N/A") -> str:
    """Format a fraction (0.93) or percentage (93.0) as '93%'."""
    if val is None or val == "N/A":
        return default
    try:
        v = float(val)
        return f"{v:.0%}" if v <= 1 else f"{v:.0f}%"
    except (ValueError, TypeError):
        return str(val)


def _units(val, default="N/A") -> str:
    if val is None or val == "N/A":
        return default
    try:
        return f"{float(val):,.0f}"
    except (ValueError, TypeError):
        return str(val)


def _dt(val, default="N/A") -> str:
    if val is None or val == "N/A":
        return default
    try:
        return f"{float(val):,.0f} DT"
    except (ValueError, TypeError):
        return str(val)


def _flag(val: bool, warn_if_true: bool = True) -> str:
    """Return a coloured flag string."""
    if val:
        return "⚠️  YES" if warn_if_true else "✅ YES"
    return "No"


# ---------------------------------------------------------------------------
# Reasoning source badge
# ---------------------------------------------------------------------------

def _reasoning_badge(source: str) -> str:
    if not source or source == "unknown":
        return "unknown"
    return source


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(result: dict) -> None:
    print("\n" + "=" * 70)
    print("BASELINE ANALYSIS REPORT")
    print("=" * 70)

    if "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        return

    report = result.get("analysis_report", {})
    stock  = report.get("stock_status", {})

    # ── Product identity ───────────────────────────────────────────────────
    print(f"\nProduct")
    print(f"  SKU                : {result.get('sku', 'N/A')}")
    print(f"  Store              : {result.get('store_id', 'N/A')}")
    print(f"  Lifecycle stage    : {stock.get('lifecycle_stage', 'N/A')}")
    print(f"  Business objective : {result.get('business_objective', 'N/A')}")

    # ── Stock status ───────────────────────────────────────────────────────
    print(f"\nStock Status")
    print(f"  Current stock      : {_units(stock.get('current_stock'))} units")
    print(f"  Lead time          : {stock.get('lead_time_avg_days', 'N/A')}d avg  "
          f"± {stock.get('lead_time_std_days', 'N/A')}d std")
    print(f"  Unit cost          : {_dt(stock.get('unit_cost'))}")
    print(f"  Holding cost       : {_pct(stock.get('holding_cost_pct'))} / year")
    print(f"  Order cost (fixed) : {_dt(stock.get('order_cost'))}")
    print(f"  Service level tgt  : {_pct(stock.get('service_level_target'))}")

    # ── Demand forecast ────────────────────────────────────────────────────
    forecast = report.get("forecast", {})
    print(f"\nDemand Forecast  (TimesFM baseline — no promo adjustment)")
    print(f"  Daily avg demand   : {forecast.get('avg_daily_demand', 'N/A')} units/day")
    print(f"  Daily std dev      : {forecast.get('demand_std_dev', 'N/A')} units/day")
    print(f"  30d total demand   : {_units(forecast.get('total_30d_demand'))} units")
    print(f"  Trend              : {forecast.get('trend_direction', 'N/A')}")

    # ── Inventory metrics ──────────────────────────────────────────────────
    metrics = report.get("metrics", {})
    avg_d   = forecast.get("avg_daily_demand", 0)
    curr    = stock.get("current_stock", 0)
    lt_avg  = stock.get("lead_time_avg_days", "?")

    print(f"\nInventory Metrics")
    print(f"  Days of stock      : {metrics.get('days_of_stock_remaining', 'N/A')}d  "
          f"({_units(curr)} units ÷ {avg_d} units/day)")
    print(f"  Effective SL       : {_pct(metrics.get('effective_service_level'))}  "
          f"(Z = {metrics.get('z_score', 'N/A')})")
    print(f"  Safety stock       : {_units(metrics.get('safety_stock'))} units  "
          f"[{_dt(metrics.get('safety_stock_cost_dt'))}]  (APICS formula)")
    print(f"  Reorder point      : {_units(metrics.get('reorder_point'))} units  "
          f"(trigger reorder when stock falls here)")
    print(f"  EOQ                : {_units(metrics.get('eoq'))} units  (economically optimal batch size)")
    print(f"  Formula order qty  : {_units(metrics.get('formula_order_qty'))} units  "
          f"[{_dt(metrics.get('total_replenishment_cost'))}]  (max(EOQ, MOQ) — input to Decision Agent)")
    print(f"  Holding cost/cycle : {_dt(metrics.get('holding_cost_per_cycle_dt'))}")

    # ── Risk assessment ────────────────────────────────────────────────────
    risk  = report.get("risk_assessment", {})
    level = risk.get("level", "N/A")
    icons = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
    print(f"\nRisk Assessment")
    print(f"  Level              : {icons.get(level, '')} {level}")
    print(f"  Overstock flag     : {_flag(risk.get('overstock_flag', False))}")
    print(f"  Rationale          : {risk.get('rationale', 'N/A')}")

    # ── Constraints ────────────────────────────────────────────────────────
    c = report.get("constraints", {})
    print(f"\nOrder Constraints")
    print(f"  MOQ                : {_units(c.get('moq'))} units")
    print(f"  MOQ binding        : {_flag(c.get('moq_is_binding', False))}")
    if c.get("moq_binding_note"):
        print(f"                       {c['moq_binding_note']}")

    print(f"\nCost Constraints")
    print(f"  High order cost    : {_flag(c.get('high_cost_flag', False))}  (threshold: 50,000 DT)")
    print(f"  High holding cost  : {_flag(c.get('high_holding_flag', False))}  (threshold: 10,000 DT / cycle)")
    print(f"  Holding cost/cycle : {_dt(c.get('holding_cost_per_cycle_dt'))}")
    print(f"  Safety stock cost  : {_dt(c.get('safety_stock_cost_dt'))}  (capital tied up permanently)")

    # ── Analyst note ───────────────────────────────────────────────────────
    print(f"\nAnalyst Note")
    print(f"  {report.get('objective_note', 'N/A')}")

    # ── Reasoning source ───────────────────────────────────────────────────
    print(f"\nReasoning source   : {_reasoning_badge(report.get('reasoning_source', 'unknown'))}")

    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def _pick(prompt: str, options: list) -> str:
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    while True:
        raw = input("  Enter number or value: ").strip()
        if raw in options:
            return raw
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print("  Invalid choice, try again.")


def interactive_mode(orchestrator):
    print("\n--- Interactive Mode ---")

    mode = _pick(
        "What do you want to analyze?",
        ["single SKU", "all SKUs in a store", "all SKUs all stores"],
    )
    objective = _pick("Business objective:", OBJECTIVES)
    stores = get_all_stores()

    if mode == "single SKU":
        store = _pick("Select store:", stores)
        skus  = get_skus_for_store(store)
        sku   = _pick("Select SKU:", skus)
        result = orchestrator.analyze_sku(sku, store, objective)
        print_report(result)

    elif mode == "all SKUs in a store":
        store = _pick("Select store:", stores)
        skus  = get_skus_for_store(store)
        print(f"\nRunning analysis for {len(skus)} SKUs in {store}...")
        results = orchestrator.analyze_batch(skus, store, objective)
        for r in results:
            print_report(r)

    else:
        skus = get_all_skus()
        print(f"\nRunning analysis for {len(skus)} SKUs across all stores...")
        confirm = input(
            f"This will run {len(skus) * len(stores)} analyses. Continue? [y/N]: "
        ).strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
        for store in stores:
            store_skus = get_skus_for_store(store)
            results = orchestrator.analyze_batch(store_skus, store, objective)
            for r in results:
                print_report(r)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory Analysis Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--sku",       help="SKU to analyze")
    parser.add_argument("--store",     help="Store ID (default: I63)", default="I63")
    parser.add_argument("--objective", help="Business objective", choices=OBJECTIVES, default="balanced")
    parser.add_argument("--all",       help="Analyze all SKUs across all stores", action="store_true")
    parser.add_argument("--list",      help="List available SKUs, stores, and objectives", action="store_true")
    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()

    print("\n" + "=" * 70)
    print("INVENTORY ANALYSIS AGENT")
    print("=" * 70)

    if args.list:
        list_available()
        return

    orchestrator = create_orchestrator()
    print("Orchestrator ready.\n")

    if not args.sku and not args.all and len(sys.argv) == 1:
        interactive_mode(orchestrator)
        return

    if args.all:
        stores = get_all_stores()
        for store in stores:
            skus = get_skus_for_store(store)
            print(f"\nStore: {store}  ({len(skus)} SKUs)")
            results = orchestrator.analyze_batch(skus, store, args.objective)
            for r in results:
                print_report(r)
        return

    if not args.sku:
        skus = get_skus_for_store(args.store)
        print(f"Analyzing all {len(skus)} SKUs in {args.store}...")
        results = orchestrator.analyze_batch(skus, args.store, args.objective)
        for r in results:
            print_report(r)
        return

    result = orchestrator.analyze_sku(args.sku, args.store, args.objective)
    print_report(result)


if __name__ == "__main__":
    main()