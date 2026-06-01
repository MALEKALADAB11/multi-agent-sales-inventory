"""
Main — Full Pipeline Test
==========================
Tests the complete three-agent pipeline (backend + DB) before touching the frontend.

Pipeline per SKU:
    analysis_agent  ┐
                    ├── parallel ──→  decision_agent → inv.recommendations
    context_agent   ┘

Usage:
    python main.py --sku ACC-BUD-001                       # single SKU, full pipeline
    python main.py --sku ACC-BUD-001 --store I63 --objective minimize_cost
    python main.py --store I63                             # all SKUs in one store
    python main.py --all                                   # all stores × all SKUs
    python main.py --sku ACC-BUD-001 --no-llm              # rule-based only, no API key needed
    python main.py --context --sku ACC-BUD-001             # context agent standalone
    python main.py --decision --sku ACC-BUD-001            # decision agent standalone (context from DB)
    python main.py --db-check                              # verify all tables reachable
    python main.py --list                                  # list SKUs, stores, objectives
"""

import sys
import argparse
import textwrap
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from src.services.orchestrator import create_orchestrator
from config.settings import STOCK_HISTORY_PATH

OBJECTIVES = ["balanced", "minimize_cost", "maximize_service_level", "clear_stock", "prioritize_margin"]


# ─── Data helpers ─────────────────────────────────────────────────────────────

def _get_all_skus() -> list:
    return sorted(pd.read_csv(STOCK_HISTORY_PATH)["sku"].unique().tolist())

def _get_all_stores() -> list:
    return sorted(pd.read_csv(STOCK_HISTORY_PATH)["store_id"].unique().tolist())

def _get_skus_for_store(store_id: str) -> list:
    df = pd.read_csv(STOCK_HISTORY_PATH)
    return sorted(df[df["store_id"] == store_id]["sku"].unique().tolist())


# ─── Formatting helpers ───────────────────────────────────────────────────────

def _f(val, fmt=".1f", suffix="", default="N/A") -> str:
    if val is None or val == "N/A":
        return default
    try:
        return f"{float(val):{fmt}}{suffix}"
    except (TypeError, ValueError):
        return str(val)

def _units(val) -> str:  return _f(val, ",.0f", " units")
def _days(val)  -> str:  return _f(val, ".1f", "d")
def _dt(val)    -> str:  return _f(val, ",.0f", " DT")
def _pct(val)   -> str:  return _f(val, "+.1f", "%")

def _flag(val: bool, warn: bool = True) -> str:
    if val:
        return "⚠  YES" if warn else "YES"
    return "No"

def _wrap(text: str, width: int = 66, indent: str = "    ") -> str:
    if not text:
        return ""
    return "\n".join(indent + line for line in textwrap.wrap(text, width))


# ─── Full pipeline report ─────────────────────────────────────────────────────

def print_report(result: dict) -> None:
    S1 = "=" * 70
    S2 = "─" * 70

    print(f"\n{S1}")
    if "error" in result and not result.get("analysis_report"):
        print(f"PIPELINE ERROR  SKU={result.get('sku')}  store={result.get('store_id')}")
        print(f"  {result['error']}")
        print(S1)
        return

    print(f"PIPELINE REPORT  |  SKU: {result['sku']}  Store: {result['store_id']}  Obj: {result['business_objective']}")

    # ── Analysis ──────────────────────────────────────────────────────────────
    report  = result.get("analysis_report", {})
    stock   = report.get("stock", {})
    fcast   = report.get("forecast", {})
    metrics = report.get("metrics", {})
    risk    = report.get("risk_assessment", {})
    cons    = report.get("constraints", {})

    level = risk.get("level", "N/A")
    icons = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}

    print(f"\n{S2}")
    print("ANALYSIS AGENT")
    print(S2)
    print(f"  Lifecycle          : {stock.get('lifecycle_stage', 'N/A')}")
    print(f"  Current stock      : {_units(stock.get('current_stock'))}")
    print(f"  Daily avg demand   : {_f(fcast.get('avg_daily_demand'), '.2f')} units/day  "
          f"(std {_f(fcast.get('demand_std_dev'), '.2f')})  trend: {fcast.get('trend_direction','N/A')}")
    print(f"  Days of stock      : {_days(metrics.get('days_of_stock_remaining'))}")
    print(f"  Safety stock       : {_units(metrics.get('safety_stock'))}  "
          f"[{_dt(metrics.get('safety_stock_cost_dt'))}]")
    print(f"  Reorder point      : {_units(metrics.get('reorder_point'))}")
    print(f"  EOQ                : {_units(metrics.get('eoq'))}")
    print(f"  Formula order qty  : {_units(metrics.get('formula_order_qty'))}  "
          f"[{_dt(metrics.get('total_replenishment_cost'))}]")
    print(f"  Holding cost/cycle : {_dt(metrics.get('holding_cost_per_cycle_dt'))}")
    print()
    print(f"  Risk level         : {icons.get(level,'')} {level}"
          f"  override={risk.get('override') or 'none'}")
    print(f"  Layer 1 (mgr thr.) : {risk.get('layer1_result', 'none triggered')}")
    print(f"  Layer 2 (LT math)  : {risk.get('layer2_result', 'N/A')}")
    print(f"  Overstock flag     : {_flag(risk.get('overstock_flag', False))}")
    print(f"  Rationale          :")
    print(_wrap(risk.get('rationale', ''), indent="    "))
    print(f"  MOQ                : {_units(cons.get('moq'))}  binding={_flag(cons.get('moq_is_binding', False))}")
    print(f"  High cost flag     : {_flag(cons.get('high_cost_flag', False))}  (>50k DT)")
    print(f"  Obj. conflict      : {_flag(cons.get('objective_conflict', False))}")
    print(f"  Objective note     : {report.get('objective_note', '')}")
    print(f"  Analyst flag       : {report.get('analyst_flag') or 'none'}")
    print(f"  Reasoning source   : {report.get('reasoning_source', 'N/A')}")

    # ── Context ───────────────────────────────────────────────────────────────
    print(f"\n{S2}")
    print("CONTEXT AGENT")
    print(S2)

    ctx = result.get("context_result", {})
    if "error" in ctx:
        print(f"  ❌ {ctx['error']}")
    elif not ctx:
        print("  (no context result)")
    else:
        cr       = ctx.get("context_report", {})
        uplift   = cr.get("demand_uplift_pct", 0.0)
        signals  = cr.get("signals", {})
        promos   = signals.get("promotions", [])
        holidays = signals.get("holidays", [])
        events   = signals.get("events", [])
        weather  = signals.get("weather", {})

        icon = "📈" if uplift > 0 else ("📉" if uplift < 0 else "➡")
        print(f"  Demand uplift      : {icon} {_pct(uplift)}")
        print(f"  Dominant signal    : {cr.get('dominant_signal', 'none')}")
        print(f"  Confidence         : {cr.get('confidence', '?')}  "
              f"source={cr.get('reasoning_source', '?')}")
        print(f"  Promotions ({len(promos)})")
        for p in promos:
            print(f"    • {p.get('promo_name','?')} — {p.get('discount_pct','?')}% off  "
                  f"[{p.get('start_date','?')} → {p.get('end_date','?')}]")
        print(f"  Holidays ({len(holidays)})")
        for h in holidays:
            print(f"    • {h.get('name','?')}  in {h.get('days_away','?')}d  "
                  f"(national={h.get('is_national','?')})")
        print(f"  Events ({len(events)})")
        for e in events:
            print(f"    • {e.get('event_name','?')}  est. uplift={e.get('estimated_uplift_pct','?')}%")
        print(f"  Weather            : {weather.get('summary','unavailable')}  "
              f"bad_days={weather.get('bad_weather_days',0)}")
        interp = cr.get("interpretation", "")
        if interp:
            print(f"  Interpretation     :")
            print(_wrap(interp))

    # ── Decision ──────────────────────────────────────────────────────────────
    print(f"\n{S2}")
    print("DECISION AGENT")
    print(S2)

    dr = result.get("decision_result", {})
    if "error" in dr:
        print(f"  ❌ {dr['error']}")
    elif not dr:
        print("  (no decision result)")
    else:
        d     = dr.get("decision", {})
        adj   = dr.get("adjusted_metrics", {})
        adj_m = adj.get("metrics") or {}

        action = d.get("action", "N/A")
        qty    = d.get("order_qty")
        action_icons = {"ORDER": "🛒", "HOLD": "✋", "MONITOR": "👁 ", "EXPEDITE": "🚨"}

        b_days = _safe_float(metrics.get("days_of_stock_remaining"))
        a_days = _safe_float(adj_m.get("days_of_stock_remaining"))
        delta  = f"  (Δ{a_days - b_days:+.1f}d from uplift)" if b_days and a_days else ""

        print(f"  ACTION             : {action_icons.get(action,'')} {action}")
        print(f"  Urgency            : {d.get('urgency','none')}")
        if qty is not None:
            print(f"  Recommended qty    : {qty:,} units")
            print(f"  Qty rationale      : {d.get('order_qty_rationale','')}")
        print()
        print(f"  Adjusted risk      : {adj.get('risk_assessment', {}).get('level', 'N/A') if isinstance(adj.get('risk_assessment'), dict) else 'N/A'}")
        print(f"  Adjusted days      : {_days(adj_m.get('days_of_stock_remaining'))}{delta}")
        print(f"  Adjusted qty       : {_units(adj_m.get('formula_order_qty'))}")
        print(f"  Adjusted cost      : {_dt(adj_m.get('total_replenishment_cost'))}")
        print()
        print(f"  Confidence         : {d.get('confidence','?')}  source={d.get('reasoning_source','?')}")
        print(f"  Trade-offs         : {d.get('trade_offs','')}")
        if d.get("escalate_to_human"):
            print(f"  ⚠  ESCALATE TO HUMAN :")
            print(_wrap(d.get('escalation_reason', ''), indent="    "))
        print(f"  Decision rationale :")
        print(_wrap(d.get('decision_rationale', '')))

        rec_id = dr.get("recommendation_id")
        if rec_id:
            print(f"\n  ✅ inv.recommendations → {rec_id}")
        elif action in ("ORDER", "EXPEDITE"):
            print(f"\n  ⚠  No recommendation_id returned (check DB write)")

    print(f"\n{S1}\n")


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ─── Standalone: context only ─────────────────────────────────────────────────

def run_context_standalone(sku: str, store_id: str, use_llm: bool) -> None:
    from src.agents.context.agent import create_context_agent
    print(f"\n[Context Agent Standalone]  SKU={sku}  store={store_id}  use_llm={use_llm}")
    agent  = create_context_agent(use_llm=use_llm)
    result = agent.run(sku=sku, store_id=store_id)

    cr = result.get("context_report", {})
    if "error" in result:
        print(f"  ❌ {result['error']}")
        return

    print(f"  uplift={_pct(cr.get('demand_uplift_pct',0))}  "
          f"dominant={cr.get('dominant_signal','none')}  "
          f"confidence={cr.get('confidence','?')}  "
          f"source={cr.get('reasoning_source','?')}")
    print(f"  Interpretation: {cr.get('interpretation','')}")

    try:
        from db.repositories.inventory_repo import SyncInventoryRepo
        row = SyncInventoryRepo.get_context_adjustment(sku=sku, store_id=store_id)
        if row:
            print(f"  ✅ inv.context_adjustments — "
                  f"uplift={row.get('demand_uplift_pct')}  "
                  f"signal={row.get('dominant_signal')}  "
                  f"valid {row.get('valid_from')} → {row.get('valid_to')}")
        else:
            print("  ⚠  No row found in inv.context_adjustments")
    except Exception as e:
        print(f"  DB check skipped: {e}")


# ─── Standalone: decision only (reads context from DB) ────────────────────────

def run_decision_standalone(sku: str, store_id: str, objective: str, use_llm: bool) -> None:
    from src.agents.decision.agent import create_decision_agent

    print(f"\n[Decision Agent Standalone]  SKU={sku}  store={store_id}  use_llm={use_llm}")

    # Get context from DB (Fix 7 DB-read path — for standalone use only)
    context_report = {}
    try:
        from db.repositories.inventory_repo import SyncInventoryRepo
        row = SyncInventoryRepo.get_context_adjustment(sku=sku, store_id=store_id)
        if row:
            context_report = {
                "demand_uplift_pct": float(row.get("demand_uplift_pct", 0.0)),
                "dominant_signal":   row.get("dominant_signal", "none"),
                "confidence":        str(row.get("confidence", 0.3)),
                "interpretation":    row.get("interpretation", ""),
                "reasoning_source":  "db_read",
            }
            print(f"  Context from DB: uplift={_pct(context_report['demand_uplift_pct'])}  "
                  f"signal={context_report['dominant_signal']}")
        else:
            print("  No context adjustment in DB — using uplift=0")
    except Exception as e:
        print(f"  DB read failed ({e}) — using uplift=0")

    # Need analysis_report — run analysis agent
    print("  Running analysis agent for baseline...")
    orch = create_orchestrator(use_llm=use_llm)
    raw  = orch.analysis_agent.run(sku=sku, store_id=store_id, business_objective=objective)
    if "error" in raw:
        print(f"  ❌ Analysis agent failed: {raw['error']}")
        return

    agent  = create_decision_agent(use_llm=use_llm)
    result = agent.run(
        sku=sku,
        store_id=store_id,
        business_objective=objective,
        analysis_report=raw.get("analysis_report", {}),
        context_report=context_report,
    )

    d      = result.get("decision", {})
    adj_m  = result.get("adjusted_metrics", {}).get("metrics") or {}
    action = d.get("action", "N/A")
    action_icons = {"ORDER": "🛒", "HOLD": "✋", "MONITOR": "👁 ", "EXPEDITE": "🚨"}

    print(f"\n  {action_icons.get(action,'')} {action}  urgency={d.get('urgency','none')}")
    if d.get("order_qty") is not None:
        print(f"  Qty: {d['order_qty']:,} units")
    print(f"  Adj. days: {_days(adj_m.get('days_of_stock_remaining'))}  "
          f"adj. qty: {_units(adj_m.get('formula_order_qty'))}")
    print(f"  Confidence: {d.get('confidence','?')}  source={d.get('reasoning_source','?')}")
    if d.get("escalate_to_human"):
        print(f"  ⚠  ESCALATE: {d.get('escalation_reason','')}")
    print(f"  Rationale: {d.get('decision_rationale','')}")

    rec_id = result.get("recommendation_id")
    if rec_id:
        print(f"\n  ✅ inv.recommendations → {rec_id}")
    elif action in ("ORDER", "EXPEDITE"):
        print("  ⚠  No recommendation_id returned")


# ─── DB check ─────────────────────────────────────────────────────────────────

def run_db_check() -> None:
    print("\n[DB Check]")
    try:
        from db.repositories.inventory_repo import SyncInventoryRepo
    except Exception as e:
        print(f"  ❌ Cannot import SyncInventoryRepo: {e}")
        return

    checks = [
        ("inv.stores",                lambda: SyncInventoryRepo.get_store("DUMMY")),
        ("inv.products",              lambda: SyncInventoryRepo.get_product("DUMMY")),
        ("inv.stock_levels",          lambda: SyncInventoryRepo.get_stock_level("DUMMY","DUMMY")),
        ("inv.agent_runs (write)",    lambda: SyncInventoryRepo.start_agent_run("analysis_agent","DUMMY")),
        ("inv.alerts",                lambda: SyncInventoryRepo.get_store_alerts("DUMMY")),
        ("inv.recommendations",       lambda: SyncInventoryRepo.get_latest_recommendation("DUMMY","DUMMY")),
        ("inv.business_objectives",   lambda: SyncInventoryRepo.get_active_objective()),
        ("inv.context_adjustments",   lambda: SyncInventoryRepo.get_context_adjustment("DUMMY","DUMMY")),
        ("inv.promotions",            lambda: None),  # no sync read yet — skip
        ("inv.events",                lambda: None),
    ]

    all_ok = True
    for name, fn in checks:
        try:
            fn()
            print(f"  ✅ {name}")
        except Exception as e:
            print(f"  ❌ {name} — {e}")
            all_ok = False

    print()
    if all_ok:
        print("All tables reachable.\n")
    else:
        print("Some tables failed — check migration and DB credentials.\n")
        print("Missing save_recommendation? Apply _repo_patch.py to inventory_repo.py.")


# ─── List ─────────────────────────────────────────────────────────────────────

def run_list() -> None:
    skus   = _get_all_skus()
    stores = _get_all_stores()
    print(f"\nSKUs ({len(skus)}):   {', '.join(skus[:15])}{'...' if len(skus)>15 else ''}")
    print(f"Stores ({len(stores)}): {', '.join(stores)}")
    print(f"Objectives: {', '.join(OBJECTIVES)}\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Inventory Pipeline — backend test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--sku",       help="SKU to analyse")
    p.add_argument("--store",     default="I63", help="Store ID (default: I63)")
    p.add_argument("--objective", default="balanced", choices=OBJECTIVES)
    p.add_argument("--all",       action="store_true", help="All stores × all SKUs")
    p.add_argument("--context",   action="store_true", help="Context agent only (needs --sku)")
    p.add_argument("--decision",  action="store_true", help="Decision agent only (needs --sku)")
    p.add_argument("--db-check",  action="store_true", dest="db_check")
    p.add_argument("--no-llm",    action="store_true",  dest="no_llm",
                   help="Rule-based only — no LLM calls, no API key needed")
    p.add_argument("--list",      action="store_true",  help="List SKUs / stores / objectives")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    use_llm = not args.no_llm

    print("\n" + "=" * 70)
    print("INVENTORY PIPELINE  |  analysis ┐")
    print("                               ├─ parallel ──→ decision")
    print("                    context    ┘")
    print("=" * 70)

    if args.list:
        run_list()
        return

    if args.db_check:
        run_db_check()
        return

    if args.context:
        if not args.sku:
            parser.error("--context requires --sku")
        run_context_standalone(args.sku, args.store, use_llm)
        return

    if args.decision:
        if not args.sku:
            parser.error("--decision requires --sku")
        run_decision_standalone(args.sku, args.store, args.objective, use_llm)
        return

    orch = create_orchestrator(use_llm=use_llm)

    if args.all:
        for store in _get_all_stores():
            skus = _get_skus_for_store(store)
            print(f"\nStore: {store}  ({len(skus)} SKUs)")
            for r in orch.analyze_batch(skus, store, args.objective):
                print_report(r)
        return

    if args.sku:
        print_report(orch.analyze_sku(args.sku, args.store, args.objective))
        return

    # No --sku → all SKUs for the given store
    skus = _get_skus_for_store(args.store)
    print(f"Analysing all {len(skus)} SKUs in {args.store}...")
    for r in orch.analyze_batch(skus, args.store, args.objective):
        print_report(r)


if __name__ == "__main__":
    main()