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

from app.inventory.tools.internal.stock_tools import (
    get_stock_status,
    get_product,
    get_sales_history,
    get_forecast,
    get_store_type,
    get_store_total_stock_units,
    _DataCache,
)
from app.inventory.agents.analysis.tools import (
    compute_demand_std,
    compute_inventory_metrics,
)
from app.inventory.agents.analysis.prompts import REASON_SYSTEM, REASON_USER

try:
    from app.inventory.forecasting.timeseries_engine import (
        forecast as ts_forecast,
        extract_series_from_sales,
    )
    _TS_ENGINE_AVAILABLE = True
    logger.info("[Analysis] TimeSeriesEngine disponible ✓")
except Exception as _e:
    _TS_ENGINE_AVAILABLE = False
    logger.info("[Analysis] TimeSeriesEngine non disponible (%s) — DB forecast utilisé", _e)

try:
    from app.inventory.pg_data_loader import get_seasonal_demand_profile
    _SEASONAL_AVAILABLE = True
except Exception:
    _SEASONAL_AVAILABLE = False

try:
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — business objective DB reads disabled.")


# ═══════════════════════════════════════════════════════════════════════════
# Boutique Storage Capacity — per-unit volume (L) and capacity by store type
# ═══════════════════════════════════════════════════════════════════════════
# categorie in sales.produits is a raw numeric code — mirrors the mapping in
# context/tools.py's _CAT_CODE_TO_NAME so both agents estimate volume off the
# same category names.
_CAT_CODE_TO_VOLUME_L: Dict[str, float] = {
    "50": 0.6,   # TERMINAL
    "70": 0.2,   # ACCESSOIRE
    "88": 0.01, "80": 0.01,  # FORFAIT
    "20": 0.01, "40": 0.01,  # SIM
    "30": 0.01, "32": 0.01,  # RECHARGE
}
_DEFAULT_UNIT_VOLUME_L = 0.1  # fallback for uncategorized/AUTRE SKUs

_STORE_CAPACITY_L = {
    "M": 5000.0,  # Mall
    "I": 2000.0,  # Official (Ooredoo-owned)
    "O": 2000.0,  # Official (partner)
    "S": 1000.0,  # Standard / other
}
_DEFAULT_STORE_CAPACITY_L = 1000.0


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

    # Sales history: PostgreSQL via pg_data_loader (3 ans, features temporelles incluses)
    # days=730 (was default 365) — required so extract_series_from_sales below can
    # actually see 2 years of history; annual-seasonality (MSTL, season_length=365)
    # is a no-op if sales_df itself is capped at 365 days. See implementation guide 1.5.
    sales_df = get_sales_history(sku, store_id, days=730)

    # Profil saisonnier 3 ans — facteurs par mois, événement, saison, jour de semaine
    seasonal_profile = {}
    if _SEASONAL_AVAILABLE:
        try:
            seasonal_profile = get_seasonal_demand_profile(sku, store_id)
        except Exception as e:
            logger.warning("Seasonal profile error for %s@%s: %s", sku, store_id, e)

    # ── Forecast : demand-sensing DB (corrected_demand) en priorité ─────────
    # inventory.demand_forecast is populated offline by run_baseline_batch.py
    # (baseline_demand, plain TS forecast) and run_sensing_job.py
    # (corrected_demand, baseline + promo/event/weather correction — signal
    # a live/local call here has no access to). When it exists for this
    # pair it's strictly richer than what this function can compute itself,
    # so it now wins. Fallback, in priority order: live TS engine
    # (StatsForecast on 730d history) -> flat baseline-1.0 placeholder.
    #
    # NOTE: the DB pipeline currently only covers the top ~400-500 sku/store
    # pairs by sales volume (TOP_N_PAIRS in backfill_baseline_forecasts.py /
    # run_baseline_batch.py — a temporary cap for the initial rollout, see
    # those files). Most pairs will fall through to the live TS path below
    # until the full backfill/sensing runs are done.
    import pandas as pd
    forecast_df = get_forecast(sku, store_id)
    has_sensing_forecast = (
        "baseline_demand" in forecast_df.columns
        and forecast_df["baseline_demand"].notna().any()
    )

    ts_result: Dict[str, Any] = {}
    if not has_sensing_forecast and _TS_ENGINE_AVAILABLE and not sales_df.empty:
        try:
            series = extract_series_from_sales(sales_df, sku, store_id, days_back=730)
            if len(series) >= 7:
                ts_result = ts_forecast(series, horizon=30)
                logger.debug(
                    "[FETCH] TS engine OK — SKU=%s engine=%s avg_daily=%.3f",
                    sku, ts_result.get("engine"), ts_result.get("avg_daily_demand"),
                )
        except Exception as exc:
            logger.warning("[FETCH] TS engine failed (%s) — flat-baseline fallback", exc)

    if has_sensing_forecast:
        forecast_source = "demand_sensing_db"
        logger.debug(
            "[FETCH] demand-sensing forecast OK — SKU=%s@%s (%d rows, "
            "corrected=%d/%d)", sku, store_id, len(forecast_df),
            int(forecast_df["corrected_demand"].notna().sum()) if "corrected_demand" in forecast_df else 0,
            len(forecast_df),
        )
    elif ts_result:
        forecast_source = "live_ts_engine"
        # Construire un forecast_df compatible depuis le résultat TS
        forecast_df = pd.DataFrame({
            "date":             pd.date_range(start=pd.Timestamp.now(), periods=30, freq="D"),
            "predicted_demand": ts_result["forecast_values"][:30],
            "sku":              sku,
            "store_id":         store_id,
        })
    else:
        forecast_source = "fallback_flat"
        if forecast_df.empty:
            logger.warning("No forecast data for %s@%s, using fallback baseline", sku, store_id)
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

    # ── Boutique storage capacity context (Part 2, section B) ───────────────
    # Cached 5min in stock_tools — one SELECT per store per cache window, not
    # per SKU, even though fetch_node runs per-SKU within a batch.
    try:
        store_type = get_store_type(store_id)
    except Exception as e:
        logger.warning("get_store_type failed for %s: %s", store_id, e)
        store_type = "S"
    try:
        store_total_stock_units = get_store_total_stock_units(store_id)
    except Exception as e:
        logger.warning("get_store_total_stock_units failed for %s: %s", store_id, e)
        store_total_stock_units = 0.0

    return {
        "fetch_data": {
            "stock":              stock_data,
            "product":            product_data,
            "sales_df":           sales_df,
            "forecast_df":        forecast_df,
            "ts_result":          ts_result,     # dict enrichi du TS engine (peut être {})
            "forecast_source":    forecast_source,  # "demand_sensing_db" | "live_ts_engine" | "fallback_flat"
            "business_objective": business_objective,
            "seasonal_profile":   seasonal_profile,
            "store_type":              store_type,
            "store_total_stock_units": store_total_stock_units,
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
    ts_result          = fetch_data.get("ts_result", {})
    forecast_source    = fetch_data.get("forecast_source", "unknown")
    business_objective = fetch_data["business_objective"]
    seasonal_profile   = fetch_data.get("seasonal_profile", {})
    store_type              = fetch_data.get("store_type", "S")
    store_total_stock_units = fetch_data.get("store_total_stock_units", 0.0)

    # ── Statistiques de demande : TS engine > saisonnier > forecast_df ───────
    if ts_result:
        # TS engine disponible — données les plus précises (StatsForecast/Chronos)
        avg_daily_demand_raw = float(ts_result["avg_daily_demand"])
        total_30d_demand_raw = float(ts_result["total_30d_demand"])
        ts_trend             = ts_result.get("trend_direction", "stable")
        ts_std               = float(ts_result.get("demand_std_dev", 0))
        ts_seasonality       = float(ts_result.get("seasonality_score", 0))
        logger.debug(
            "[COMPUTE] TS engine data — SKU=%s avg=%.3f std=%.3f trend=%s seasonality=%.2f",
            sku, avg_daily_demand_raw, ts_std, ts_trend, ts_seasonality,
        )
    else:
        avg_daily_demand_raw = float(forecast_df["predicted_demand"].mean())
        total_30d_demand_raw = float(forecast_df["predicted_demand"].sum())
        ts_trend             = None
        ts_std               = 0.0
        ts_seasonality       = 0.0

    # ── Ajustement saisonnier depuis le profil 3 ans ───────────────────────
    import datetime
    current_month = datetime.date.today().month
    seasonal_uplift = 1.0

    if seasonal_profile and seasonal_profile.get("baseline_demand", 0) > 0:
        # Préférer la baseline observée hors-events comme référence de demande
        baseline = seasonal_profile["baseline_demand"]
        month_factor = seasonal_profile.get("by_month", {}).get(current_month, 1.0)
        seasonal_uplift = month_factor

        # Utiliser la baseline × facteur mois comme demande attendue si plus riche que forecast_df
        n_years = seasonal_profile.get("n_data_years", 0)
        if n_years >= 2 and baseline > 0:
            avg_daily_demand = baseline * month_factor
            total_30d_demand = avg_daily_demand * 30
            logger.debug(
                "[COMPUTE] SKU=%s: seasonal baseline %.3f × month_factor %.3f → %.3f",
                sku, baseline, month_factor, avg_daily_demand
            )
        else:
            avg_daily_demand = avg_daily_demand_raw
            total_30d_demand = total_30d_demand_raw
    else:
        avg_daily_demand = avg_daily_demand_raw
        total_30d_demand = total_30d_demand_raw

    # ── Trend : TS engine > profil saisonnier > forecast_df ─────────────────
    if ts_trend:
        # TS engine: tendance calculée sur régression linéaire 14j
        trend = ts_trend
    else:
        trend_6m_pct = seasonal_profile.get("trend_6m_pct", 0) if seasonal_profile else 0
        if trend_6m_pct > 10:
            trend = "increasing"
        elif trend_6m_pct < -10:
            trend = "declining"
        elif len(forecast_df) >= 14:
            df_sorted = forecast_df.sort_values("date")
            first_7   = df_sorted.head(7)["predicted_demand"].mean()
            last_7    = df_sorted.tail(7)["predicted_demand"].mean()
            trend = "increasing" if last_7 > first_7 * 1.1 else ("declining" if last_7 < first_7 * 0.9 else "stable")
        else:
            trend = "stable"

    # ── Écart-type demande : TS engine > profil saisonnier > compute ────────
    if ts_std > 0:
        demand_std = ts_std * seasonal_uplift
    elif seasonal_profile and seasonal_profile.get("demand_std", 0) > 0:
        demand_std = seasonal_profile["demand_std"] * seasonal_uplift
    else:
        demand_std = compute_demand_std(sales_df, avg_daily_demand)

    if product.get("unit_cost") is None:
        logger.warning(
            "[COMPUTE] SKU=%s: unit_cost is NULL in inventory.products — "
            "defaulting to 0 (cost-based metrics for this SKU will be unreliable "
            "until the row is backfilled)", sku
        )

    # ── Boutique storage capacity estimate (Part 2, section B) ──────────────
    unit_volume_l = _CAT_CODE_TO_VOLUME_L.get(str(product.get("category", "")), _DEFAULT_UNIT_VOLUME_L)
    store_capacity_l = _STORE_CAPACITY_L.get(
        (store_type or "S")[:1].upper(), _DEFAULT_STORE_CAPACITY_L
    )
    store_space_utilization_pct = (
        (store_total_stock_units * unit_volume_l / store_capacity_l) * 100
        if store_capacity_l > 0 else 0.0
    )

    supplier_order_multiple = int(product.get("supplier_order_multiple", 1) or 1)

    # Compute all metrics
    metrics = compute_inventory_metrics(
        # Stock
        stock_current      = stock.get("stock_current") or 0,
        stock_in_transit   = stock.get("stock_in_transit") or 0,
        stock_min          = stock.get("stock_min"),
        stock_max          = stock.get("stock_max"),
        # Product
        lead_time_avg      = float(product["lead_time_days"]),
        lead_time_std      = float(product.get("lead_time_std", 0) or 0),
        moq                = float(product["moq"] or 0),
        unit_cost          = float(product.get("unit_cost") or 0),
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
        # Supplier lot size (Part 2, section A)
        supplier_order_multiple = supplier_order_multiple,
    )

    # ── Enrich metrics["stock"] with supplier/product/capacity context so the
    # decision agent (and reason_node below) can read it straight off
    # baseline_report["stock"] without re-querying anything. ─────────────────
    metrics["stock"].update({
        "flag_4g":                     bool(product.get("flag_4g", False)),
        "flag_5g":                     bool(product.get("flag_5g", False)),
        "brand":                       product.get("brand", "") or "",
        "preferred_supplier_id":       product.get("preferred_supplier_id"),
        "preferred_supplier_name":     product.get("preferred_supplier_name"),
        "preferred_supplier_reliable": bool(product.get("preferred_supplier_reliable", True)),
        "preferred_supplier_active":   bool(product.get("preferred_supplier_active", True)),
        "fallback_supplier_name":      product.get("fallback_supplier_name"),
        "fallback_supplier_active":    bool(product.get("fallback_supplier_active", False)),
        "store_type":                  store_type,
        "store_space_utilization_pct": store_space_utilization_pct,
    })

    # Enrichir les métriques avec les données TS engine si disponibles
    if ts_result:
        metrics.setdefault("forecast", {}).update({
            "confidence_lower":  ts_result.get("confidence_lower", []),
            "confidence_upper":  ts_result.get("confidence_upper", []),
            "seasonality_score": ts_seasonality,
            "forecast_engine":   ts_result.get("engine", "unknown"),
        })
    metrics.setdefault("forecast", {})["forecast_source"] = forecast_source

    return {
        "computed_metrics":    metrics,
        "business_objective":  business_objective,
        "seasonal_profile":    seasonal_profile,
        "seasonal_uplift":     seasonal_uplift,
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
            # Supplier / store / product context (Part 2)
            preferred_supplier_name     = metrics["stock"].get("preferred_supplier_name") or "N/A",
            preferred_supplier_active   = metrics["stock"].get("preferred_supplier_active", True),
            preferred_supplier_reliable = metrics["stock"].get("preferred_supplier_reliable", True),
            fallback_supplier_name      = metrics["stock"].get("fallback_supplier_name") or "aucun",
            supplier_order_multiple     = metrics["constraints"].get("supplier_order_multiple", 1),
            store_type                  = metrics["stock"].get("store_type", "?"),
            store_space_utilization_pct = metrics["stock"].get("store_space_utilization_pct", 0.0),
            brand                       = metrics["stock"].get("brand", ""),
            flag_4g                     = metrics["stock"].get("flag_4g", False),
            flag_5g                     = metrics["stock"].get("flag_5g", False),
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