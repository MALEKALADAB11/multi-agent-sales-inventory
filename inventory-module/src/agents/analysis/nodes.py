"""
Analysis Agent Nodes
=====================
LangGraph nodes for the analysis agent workflow.
Three nodes: fetch → compute → reason
"""

import json
import logging
from typing import Dict, Any
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from src.tools.internal.stock_tools import (
    get_stock_status,
    get_product,
    get_sales_history,
    get_forecast,
    _DataCache,
)
from src.agents.analysis.tools import (
    compute_demand_std,
    compute_inventory_metrics,
)
from src.agents.analysis.prompts import REASON_SYSTEM, REASON_USER

try:
    from db.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — business objective DB reads disabled.")


# ═══════════════════════════════════════════════════════════════════════════
# Fetch Node — Pure Python data gathering
# ═══════════════════════════════════════════════════════════════════════════

def fetch_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch all data needed for analysis.
    Uses preloaded_stock / preloaded_product from state when available
    (injected by orchestrator batch pre-fetch) to avoid per-SKU DB queries.
    """
    sku      = state["sku"]
    store_id = state["store_id"]

    logger.debug("[FETCH] SKU=%s, Store=%s", sku, store_id)

    # Stock data: use pre-fetched batch data if available, else DB → CSV
    preloaded_stock = state.get("preloaded_stock") or {}
    if preloaded_stock:
        stock_current = float(preloaded_stock.get(sku, 0))
        # Check for live sale override
        override = _DataCache._stock_overrides.get((sku, store_id))
        if override is not None:
            stock_current = override
        stock_data = {
            "stock_current":    stock_current,
            "stock_in_transit": 0.0,
            "stock_min":        None,
            "stock_max":        None,
            "source":           "preloaded_batch",
        }
    else:
        stock_data = get_stock_status(sku, store_id)

    # Product data: use pre-fetched batch data if available, else DB → CSV
    preloaded_product = state.get("preloaded_product") or {}
    if preloaded_product:
        product_data = preloaded_product
    else:
        product_data = get_product(sku)
    if not product_data:
        raise ValueError(f"No product data found for SKU {sku}")

    # Sales history: always CSV (cached in _DataCache — fast filter on loaded DF)
    sales_df = get_sales_history(sku, store_id)

    # Forecast: always CSV (cached in _DataCache — fast filter on loaded DF)
    forecast_df = get_forecast(sku, store_id)
    if forecast_df.empty:
        logger.warning("No forecast data for %s@%s, using fallback baseline", sku, store_id)
        import pandas as pd
        forecast_df = pd.DataFrame({
            "date":             pd.date_range(start=pd.Timestamp.now(), periods=30, freq="D"),
            "predicted_demand": 1.0,
            "sku":              sku,
            "store_id":         store_id,
        })

    # Business objective: already resolved once by orchestrator and passed in state.
    # Only hit DB here for single-SKU calls (no preloaded batch context).
    business_objective = state.get("business_objective", "balanced")
    if not preloaded_stock and _DB_AVAILABLE:
        try:
            obj = SyncInventoryRepo.get_active_objective()
            if obj:
                business_objective = obj.get("label") or obj.get("objective_type") or business_objective
        except Exception as e:
            logger.warning("Failed to get business objective from DB: %s", e)

    return {
        "fetch_data": {
            "stock":              stock_data,
            "product":            product_data,
            "sales_df":           sales_df,
            "forecast_df":        forecast_df,
            "business_objective": business_objective,
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
# Compute Node — Pure Python metric computation
# ═══════════════════════════════════════════════════════════════════════════

def compute_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute all inventory metrics from fetched data.
    No LLM, no data access — pure math.
    Returns state update with 'computed_metrics' dict.
    """
    sku        = state["sku"]
    store_id   = state["store_id"]
    fetch_data = state["fetch_data"]

    logger.debug("[COMPUTE] SKU=%s", sku)

    stock              = fetch_data["stock"]
    product            = fetch_data["product"]
    forecast_df        = fetch_data["forecast_df"]
    sales_df           = fetch_data["sales_df"]
    business_objective = fetch_data["business_objective"]

    # Demand statistics from forecast
    avg_daily_demand = float(forecast_df["predicted_demand"].mean())
    total_30d_demand = float(forecast_df["predicted_demand"].sum())

    # Trend: compare first 7 days vs last 7 days
    if len(forecast_df) >= 14:
        df_sorted = forecast_df.sort_values("date")
        first_7   = df_sorted.head(7)["predicted_demand"].mean()
        last_7    = df_sorted.tail(7)["predicted_demand"].mean()
        if last_7 > first_7 * 1.1:
            trend = "increasing"
        elif last_7 < first_7 * 0.9:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "stable"

    # Demand std dev from historical sales (daily-grouped for correctness)
    demand_std = compute_demand_std(sales_df, avg_daily_demand)

    # Compute all metrics
    metrics = compute_inventory_metrics(
        # Stock
        stock_current      = stock["stock_current"],
        stock_in_transit   = stock["stock_in_transit"],
        stock_min          = stock["stock_min"],
        stock_max          = stock["stock_max"],
        # Product
        lead_time_avg      = float(product["lead_time_days"]),
        lead_time_std      = float(product.get("lead_time_std", 0) or 0),
        moq                = float(product["moq"]),
        unit_cost          = float(product["unit_cost"]),
        holding_cost_pct   = float(product.get("holding_cost_pct", 0.25) or 0.25),
        order_cost         = float(product.get("order_cost", 50.0) or 50.0),
        lifecycle_stage    = str(product.get("lifecycle_stage", "mature") or "mature"),
        service_level_target = float(product.get("service_level_target", 0.95) or 0.95),
        # Demand
        avg_daily_demand   = avg_daily_demand,
        demand_std         = demand_std,
        total_30d_demand   = total_30d_demand,
        trend_direction    = trend,
        # Business context
        business_objective = business_objective,
    )

    return {
        "computed_metrics":    metrics,
        "business_objective":  business_objective,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Reason Node — LLM evaluates rule-based output for conflicts
# ═══════════════════════════════════════════════════════════════════════════

def create_reason_node(llm, use_llm: bool = True):
    """
    Factory: creates the reason node with LLM injected.

    The LLM's role here is NOT narration — the rules already computed
    everything correctly. The LLM acts as an evaluator: it sees the full
    picture (metrics, risk, constraints, objective, lifecycle all at once)
    and detects cross-dimensional conflicts the rule-based layers cannot see.

    It can DOWNGRADE, ESCALATE, or CONFIRM risk, flag objective conflicts,
    and surface one-sentence analyst insights. Rule-based fallback produces
    the same JSON structure with nulls so downstream sees no difference.
    """

    def reason_node(state: Dict[str, Any]) -> Dict[str, Any]:
        sku     = state["sku"]
        metrics = state["computed_metrics"]

        logger.debug("[REASON] SKU=%s, use_llm=%s", sku, use_llm)

        # Path 1: LLM disabled
        if not use_llm:
            return _apply_rule_based_fallback(metrics, "rule_based (use_llm=False)")

        # Paths 2 & 3: attempt LLM with hard timeout
        prompt = REASON_USER.format(
            sku                    = sku,
            store_id               = state["store_id"],
            business_objective     = state.get("business_objective", "balanced"),
            current_stock          = metrics["stock"]["current_stock"],
            stock_in_transit       = metrics["stock"]["stock_in_transit"],
            stock_min              = metrics["stock"]["stock_min"],
            stock_max              = metrics["stock"]["stock_max"],
            lifecycle_stage        = metrics["stock"]["lifecycle_stage"],
            avg_daily_demand       = metrics["forecast"]["avg_daily_demand"],
            demand_std_dev         = metrics["forecast"]["demand_std_dev"],
            total_30d_demand       = metrics["forecast"]["total_30d_demand"],
            trend_direction        = metrics["forecast"]["trend_direction"],
            days_remaining         = metrics["metrics"]["days_of_stock_remaining"],
            effective_service_level= f"{metrics['metrics']['effective_service_level']:.0%}",
            z_score                = metrics["metrics"]["z_score"],
            safety_stock           = metrics["metrics"]["safety_stock"],
            safety_stock_cost      = metrics["metrics"]["safety_stock_cost_dt"],
            reorder_point          = metrics["metrics"]["reorder_point"],
            eoq                    = metrics["metrics"]["eoq"],
            formula_order_qty      = metrics["metrics"]["formula_order_qty"],
            total_replenishment_cost = metrics["metrics"]["total_replenishment_cost"],
            holding_cost_per_cycle = metrics["metrics"]["holding_cost_per_cycle_dt"],
            risk_level             = metrics["risk_assessment"]["level"],
            threshold_triggered    = metrics["risk_assessment"]["threshold_triggered"],
            overstock_flag         = metrics["risk_assessment"]["overstock_flag"],
            layer1_result          = metrics["risk_assessment"]["layer1_result"] or "N/A",
            layer2_result          = metrics["risk_assessment"]["layer2_result"],
            risk_rationale         = metrics["risk_assessment"]["rationale"],
            moq                    = metrics["constraints"]["moq"],
            moq_is_binding         = metrics["constraints"]["moq_is_binding"],
            high_cost_flag         = metrics["constraints"]["high_cost_flag"],
            high_holding_flag      = metrics["constraints"]["high_holding_flag"],
        )

        try:
            response        = llm.invoke([
                SystemMessage(content=REASON_SYSTEM),
                HumanMessage(content=prompt),
            ])
            reasoning       = _parse_llm_response(response.content)
            updated_metrics = dict(metrics)
            _apply_llm_reasoning(updated_metrics, reasoning)
            updated_metrics["reasoning_source"] = f"llm ({llm.__class__.__name__})"
            logger.debug("[REASON] LLM OK for SKU=%s", sku)
            return {
                "computed_metrics": updated_metrics,
                "messages": state.get("messages", []) + [response],
            }

        except Exception as e:
            logger.warning("[REASON] LLM error for SKU=%s: %s — fallback", sku, e)
            return _apply_rule_based_fallback(metrics, f"rule_based (llm_error: {type(e).__name__})")

    return reason_node


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _parse_llm_response(content: str) -> Dict[str, Any]:
    """Parse JSON response from LLM, stripping markdown fences if present."""
    content = content.strip()
    if content.startswith("```"):
        lines   = content.split("\n")
        content = "\n".join(lines[1:-1])

    try:
        data = json.loads(content)
        return {
            "objective_note":         data.get("objective_note", ""),
            "risk_rationale":         data.get("risk_rationale", ""),
            "risk_override":          data.get("risk_override"),
            "override_reason":        data.get("override_reason"),
            "objective_conflict":     data.get("objective_conflict", False),
            "objective_conflict_note":data.get("objective_conflict_note"),
            "analyst_flag":           data.get("analyst_flag"),
        }
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse LLM JSON: %s", e)
        return {
            "objective_note": "", "risk_rationale": "",
            "risk_override": None, "override_reason": None,
            "objective_conflict": False, "objective_conflict_note": None,
            "analyst_flag": None,
        }


def _apply_llm_reasoning(metrics: Dict[str, Any], reasoning: Dict[str, Any]) -> None:
    """Apply LLM evaluation output to computed metrics dict (mutates in place)."""
    metrics["objective_note"] = reasoning.get("objective_note", "")

    if reasoning.get("risk_rationale"):
        metrics["risk_assessment"]["rationale"] = reasoning["risk_rationale"]

    # Risk override — LLM saw a cross-dimensional conflict the rules missed
    override = reasoning.get("risk_override")
    if override in ["DOWNGRADE", "ESCALATE", "CONFIRM"]:
        raw_level = metrics["risk_assessment"]["level"]
        metrics["risk_assessment"]["raw_level"]      = raw_level
        metrics["risk_assessment"]["override"]       = override
        metrics["risk_assessment"]["override_reason"]= reasoning.get("override_reason", "")

        if override == "DOWNGRADE":
            # Step down one level (CRITICAL→HIGH, HIGH→MEDIUM, MEDIUM→LOW)
            level_map = {"CRITICAL": "HIGH", "HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}
            metrics["risk_assessment"]["level"] = level_map.get(raw_level, raw_level)

        elif override == "ESCALATE":
            # Step up one level (LOW→MEDIUM, MEDIUM→HIGH, HIGH→CRITICAL)
            level_map = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "CRITICAL", "CRITICAL": "CRITICAL"}
            metrics["risk_assessment"]["level"] = level_map.get(raw_level, raw_level)

        # CONFIRM: level unchanged, just marks LLM agreed with rules

    metrics["constraints"]["objective_conflict"]      = reasoning.get("objective_conflict", False)
    metrics["constraints"]["objective_conflict_note"] = reasoning.get("objective_conflict_note")
    metrics["analyst_flag"]                           = reasoning.get("analyst_flag")


def _apply_rule_based_fallback(metrics: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """
    Generate rule-based reasoning when LLM is disabled or unavailable.
    Produces the same JSON structure with nulls — downstream sees no difference.
    """
    updated = dict(metrics)

    updated["objective_note"] = (
        f"Objective '{metrics.get('business_objective', 'balanced')}' with "
        f"lifecycle '{metrics['stock']['lifecycle_stage']}' set effective SL to "
        f"{metrics['metrics']['effective_service_level']:.0%}."
    )

    # Risk rationale already set by compute node — preserve it
    updated["risk_assessment"] = dict(metrics["risk_assessment"])
    updated["risk_assessment"]["override"]        = None
    updated["risk_assessment"]["override_reason"] = None

    updated["constraints"] = dict(metrics["constraints"])
    updated["constraints"]["objective_conflict"]       = False
    updated["constraints"]["objective_conflict_note"]  = None

    updated["analyst_flag"]      = None
    updated["reasoning_source"]  = reason

    return {"computed_metrics": updated}