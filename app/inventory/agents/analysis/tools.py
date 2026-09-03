"""
Analysis Agent Tools
====================
Pure computation functions for inventory metrics.
No data access — all inputs are passed as arguments.

compute_demand_std now accepts a DataFrame directly (passed from the fetch
node) instead of re-fetching from cache. This gives proper daily-grouped
std dev matching the old _demand_std() behaviour.
"""

import math
import logging
import pandas as pd
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Service Level & Z-Score
# ═══════════════════════════════════════════════════════════════════════════

def service_level_to_z(service_level: float) -> float:
    """
    Convert service level (fill rate) to Z-score.
    Uses scipy if available, otherwise lookup table.
    """
    try:
        from scipy.stats import norm
        return float(norm.ppf(service_level))
    except ImportError:
        table = {
            0.75: 0.674, 0.80: 0.842, 0.85: 1.036,
            0.90: 1.282, 0.92: 1.405, 0.93: 1.476,
            0.95: 1.645, 0.96: 1.751, 0.97: 1.881,
            0.98: 2.054, 0.99: 2.326, 0.995: 2.576,
        }
        nearest = min(table.keys(), key=lambda k: abs(k - service_level))
        return table[nearest]


def compute_effective_service_level(
    base_service_level: float,
    lifecycle_stage: str,
    business_objective: str,
) -> Tuple[float, str]:
    """
    Adjust base service level based on lifecycle and objective.

    Uses additive adjustments (matching old _effective_service_level logic)
    then clips to objective-specific bounds.

    Lifecycle stage strings cover both old CSV values and new DB seeds:
        introduction / new_launch → +0.03
        growth                    → +0.02
        mature                    → ±0.00
        decline                   → −0.05
        end_of_life / phase_out   → −0.10

    Returns:
        (effective_service_level, explanation_text)
    """
    sl = base_service_level

    lifecycle_adjustments = {
        "introduction": +0.03,
        "new_launch":   +0.03,
        "growth":       +0.02,
        "mature":        0.00,
        "decline":      -0.05,
        "end_of_life":  -0.10,
        "phase_out":    -0.10,
    }

    stage_key = lifecycle_stage.lower().replace(" ", "_")
    adj       = lifecycle_adjustments.get(stage_key, 0.0)
    if adj != 0.0:
        sl += adj

    obj_settings = {
        "cost":          {"min": 0.75,                          "max": base_service_level},
        "cost_savings":  {"min": 0.75,                          "max": base_service_level},
        "balanced":      {"min": base_service_level,            "max": base_service_level},
        "standard":      {"min": base_service_level,            "max": base_service_level},
        "service_level": {"min": max(base_service_level, 0.98), "max": 0.999},
        "high_demand":   {"min": max(base_service_level, 0.98), "max": 0.999},
        "competitive":   {"min": max(base_service_level, 0.95), "max": 0.999},
        "market_growth": {"min": max(base_service_level, 0.95), "max": 0.999},
    }

    bounds     = obj_settings.get(business_objective.lower(),
                                  {"min": base_service_level, "max": base_service_level})
    sl_clipped = max(bounds["min"], min(sl, bounds["max"]))
    sl_final   = max(0.70, min(sl_clipped, 0.999))

    notes = []
    if adj != 0.0:
        direction = "boosted" if adj > 0 else "reduced"
        notes.append(f"lifecycle={lifecycle_stage} ({direction} by {abs(adj):.0%})")
    if sl_clipped != sl:
        notes.append(f"objective={business_objective} clipped {sl:.2%}→{sl_clipped:.2%}")

    explanation = (
        f"Base={base_service_level:.0%}, adjusted to {sl_final:.0%}"
        + (f" ({'; '.join(notes)})" if notes else " (no adjustments)")
    )

    return sl_final, explanation


# ═══════════════════════════════════════════════════════════════════════════
# Demand Statistics
# ═══════════════════════════════════════════════════════════════════════════

def compute_demand_std(
    sales_df: pd.DataFrame,
    avg_daily_demand: float,
    lookback_days: int = 90,
) -> float:
    """
    Compute daily demand standard deviation from sales history.

    Groups by date and sums quantity_sold (matching old _demand_std logic),
    giving a proper daily std dev rather than a row-level std dev.

    Falls back to 30% of mean if:
      - DataFrame is empty
      - Fewer than 14 rows after filtering
      - Computed std is NaN or effectively zero
    """
    if sales_df.empty or len(sales_df) < 14:
        return avg_daily_demand * 0.30

    try:
        df      = sales_df.sort_values("date").tail(lookback_days)
        daily   = df.groupby("date")["quantity_sold"].sum()
        std_dev = float(daily.std())

        if math.isnan(std_dev) or std_dev < 0.01:
            return avg_daily_demand * 0.30

        return std_dev

    except Exception:
        return avg_daily_demand * 0.30


# ═══════════════════════════════════════════════════════════════════════════
# Safety Stock (APICS Formula)
# ═══════════════════════════════════════════════════════════════════════════

def compute_safety_stock(
    z_score: float,
    lead_time_avg: float,
    lead_time_std: float,
    avg_daily_demand: float,
    demand_std: float,
) -> float:
    """
    APICS safety stock formula:
    SS = Z × sqrt(LT_avg × σ_demand² + demand_avg² × σ_LT²)
    """
    variance     = (
        lead_time_avg * (demand_std ** 2) +
        (avg_daily_demand ** 2) * (lead_time_std ** 2)
    )
    safety_stock = z_score * math.sqrt(variance)
    return max(0.0, safety_stock)


# ═══════════════════════════════════════════════════════════════════════════
# EOQ
# ═══════════════════════════════════════════════════════════════════════════

def compute_eoq(
    avg_daily_demand: float,
    unit_cost: float,
    holding_cost_pct: float,
    order_cost: float,
) -> float:
    """
    EOQ = sqrt(2 × annual_demand × order_cost / (holding_cost_pct × unit_cost))
    Returns 0.0 if any parameter is invalid.
    """
    annual_demand = avg_daily_demand * 365

    # unit_cost=0 arrive pour les SKUs SIM/Forfait sans prix dans product_master.csv
    # Utiliser un coût minimal de 1.0 TND pour permettre le calcul EOQ
    if unit_cost <= 0:
        unit_cost = 1.0
    if holding_cost_pct <= 0:
        holding_cost_pct = 0.25  # 25% default
    if order_cost <= 0:
        order_cost = 50.0        # 50 TND default

    if avg_daily_demand <= 0:
        return 0.0  # pas de demande → pas de commande, silencieux

    return max(0.0, math.sqrt(
        (2 * annual_demand * order_cost) / (holding_cost_pct * unit_cost)
    ))


# ═══════════════════════════════════════════════════════════════════════════
# Risk Classification — Two Layers
# ═══════════════════════════════════════════════════════════════════════════

def classify_risk(
    days_remaining: float,
    stock_current: float,
    stock_in_transit: float,
    stock_min: Optional[float],
    stock_max: Optional[float],
    lead_time_avg: float,
    lead_time_std: float,
) -> Dict[str, Any]:
    """
    Two-layer risk classification:

    Layer 1 (Manager Thresholds — checked first):
      stock_current + stock_in_transit <= stock_min → CRITICAL
      stock_current + stock_in_transit >= stock_max → overstock flag

    Layer 2 (Lead-Time Math — checked second):
      days < lead_time_avg               → CRITICAL
      days < lead_time_avg + 2×std       → HIGH
      days < lead_time_avg × 2.5         → MEDIUM
      else                               → LOW

    Final risk = higher of the two layers.
    """
    total_stock = stock_current + stock_in_transit

    layer1_risk      = None
    layer1_rationale = None
    overstock_flag   = False
    threshold        = "lead_time_math"

    if stock_min is not None and total_stock <= stock_min:
        layer1_risk      = "CRITICAL"
        layer1_rationale = (
            f"Total stock ({total_stock:.0f}) at or below manager minimum "
            f"({stock_min:.0f}). Immediate replenishment required."
        )
        threshold = "stock_min"

    if stock_max is not None and total_stock >= stock_max:
        overstock_flag = True
        if layer1_rationale:
            layer1_rationale += f" Also at or above maximum threshold ({stock_max:.0f})."
        elif threshold != "stock_min":
            threshold = "stock_max"

    lt_variability_buffer = lead_time_std * 2

    if days_remaining < lead_time_avg:
        layer2_risk      = "CRITICAL"
        layer2_rationale = (
            f"Stock ({days_remaining:.1f}d) will run out before next order arrives "
            f"(lead time {lead_time_avg:.0f}d). Stockout imminent."
        )
    elif days_remaining < lead_time_avg + lt_variability_buffer:
        layer2_risk      = "HIGH"
        layer2_rationale = (
            f"Stock ({days_remaining:.1f}d) within lead time variability window "
            f"({lead_time_avg:.0f}d avg ± {lead_time_std:.1f}d std). "
            f"A delayed shipment would cause a stockout."
        )
    elif days_remaining < lead_time_avg * 2.5:
        layer2_risk      = "MEDIUM"
        layer2_rationale = (
            f"Stock ({days_remaining:.1f}d) above variability window but below "
            f"2.5× lead time ({lead_time_avg * 2.5:.0f}d). Monitor and plan."
        )
    else:
        layer2_risk      = "LOW"
        layer2_rationale = (
            f"Stock ({days_remaining:.1f}d) exceeds 2.5× lead time "
            f"({lead_time_avg * 2.5:.0f}d). Well covered."
        )

    risk_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

    if layer1_risk and risk_rank[layer1_risk] >= risk_rank[layer2_risk]:
        final_level     = layer1_risk
        final_rationale = layer1_rationale
    else:
        final_level     = layer2_risk
        final_rationale = layer2_rationale

    if overstock_flag:
        final_rationale += f" WARNING: {days_remaining:.0f} days of stock — excess capital tied up."

    return {
        "level":               final_level,
        "raw_level":           final_level,
        "override":            None,
        "override_reason":     None,
        "overstock_flag":      overstock_flag,
        "threshold_triggered": threshold,
        "rationale":           final_rationale,
        "layer1_result":       f"{layer1_risk}: {layer1_rationale}" if layer1_risk else None,
        "layer2_result":       f"{layer2_risk}: {layer2_rationale}",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Master Compute Function
# ═══════════════════════════════════════════════════════════════════════════

def compute_inventory_metrics(
    # Stock
    stock_current: float,
    stock_in_transit: float,
    stock_min: Optional[float],
    stock_max: Optional[float],
    # Product
    lead_time_avg: float,
    lead_time_std: float,
    moq: float,
    unit_cost: float,
    holding_cost_pct: float,
    order_cost: float,
    lifecycle_stage: str,
    service_level_target: float,
    # Demand
    avg_daily_demand: float,
    demand_std: float,
    total_30d_demand: float,
    trend_direction: str,
    # Business context
    business_objective: str,
    # Optional promo uplift for decision agent re-computation
    promo_uplift_pct: float = 0.0,
    # How many of the next days promo_uplift_pct actually applies to (1-30).
    # Default 7 preserves the old "week-ish" assumption for callers that
    # don't pass it. The decision agent passes the context agent's
    # impact_window_days here instead of always assuming the full 30 days.
    impact_window_days: float = 7.0,
    # Temporary safety-stock bump (%) for situational uncertainty — distinct
    # from demand size. Comes from the context agent's context_volatility
    # read. 0 = no bump (default, backward compatible).
    buffer_recommendation_pct: float = 0.0,
    # Supplier lot size (commande_multiple) — order qty rounds up to the
    # nearest multiple of this when >1. Default 1 = no rounding (no supplier
    # data available, e.g. old-format products without supplier_products rows).
    supplier_order_multiple: int = 1,
) -> Dict[str, Any]:
    """
    Compute all inventory metrics.
    Pure computation — no data access, no LLM, no side effects.

    promo_uplift_pct: used by the decision agent when re-running with
                      context adjustment applied. Pass 0 for baseline.
                      Whatever discount/mitigation is appropriate for a
                      forecast_source that already bakes in some of this
                      (e.g. ML Demand Sensing) must be applied by the CALLER
                      before this function ever sees promo_uplift_pct — this
                      function has no notion of forecast_source and applies
                      whatever value it's given, in full, prorated over
                      impact_window_days.

    impact_window_days: the uplift is prorated over this many days (capped
                      to the 30-day horizon) rather than applied uniformly
                      across all 30 days. A 3-day event no longer inflates
                      the full month — see additional_units below.

    Returns structured dict matching the analysis_report format.
    """
    if promo_uplift_pct != 0.0:
        days_capped = min(max(impact_window_days, 1.0), 30.0)

        # Only the days actually touched by the signal get the elevated
        # rate — the remaining days of the 30-day horizon stay at baseline.
        # This is the fix for the "7-day signal applied to 30 days of
        # demand" bias: a 3-day storm no longer inflates the whole month.
        additional_units = avg_daily_demand * (promo_uplift_pct / 100.0) * days_capped
        total_30d_demand = total_30d_demand + additional_units

        # avg_daily_demand becomes the blended rate over the full 30-day
        # horizon (elevated for days_capped days, baseline for the rest),
        # since safety stock / EOQ / reorder point all key off a single
        # avg_daily_demand value applied across the whole lead time —
        # blending keeps that single number honest instead of assuming
        # the elevated rate holds indefinitely.
        if total_30d_demand > 0:
            avg_daily_demand = total_30d_demand / 30.0

    eff_sl, sl_explanation = compute_effective_service_level(
        service_level_target, lifecycle_stage, business_objective
    )

    z_score      = service_level_to_z(eff_sl)
    safety_stock = compute_safety_stock(
        z_score, lead_time_avg, lead_time_std, avg_daily_demand, demand_std
    )

    # Situational buffer: widen safety stock for uncertainty in the *read*
    # (contradictory signals, thin history, novel events) — separate from
    # the demand-size adjustment above. 0% is a no-op, fully backward
    # compatible with callers that don't pass this.
    if buffer_recommendation_pct:
        safety_stock = safety_stock * (1.0 + max(0.0, buffer_recommendation_pct) / 100.0)

    reorder_point     = avg_daily_demand * lead_time_avg + safety_stock
    eoq               = compute_eoq(avg_daily_demand, unit_cost, holding_cost_pct, order_cost)
    formula_order_qty = max(moq, round(eoq))

    # Round up to the supplier's lot size (commande_multiple) so the order
    # is actually placeable — e.g. MOQ=50, EOQ=65, lot=20 → order 80, not 65.
    lot_size_rounded = False
    if supplier_order_multiple and supplier_order_multiple > 1:
        remainder = formula_order_qty % supplier_order_multiple
        if remainder:
            formula_order_qty += (supplier_order_multiple - remainder)
            lot_size_rounded = True

    # Sentinelle 999 = "couverture illimitée" — valable UNIQUEMENT si du stock
    # existe sans demande. Stock nul ⇒ couverture nulle, quelle que soit la demande.
    if avg_daily_demand > 0:
        days_remaining = stock_current / avg_daily_demand
    elif stock_current > 0:
        days_remaining = 999.0
    else:
        days_remaining = 0.0

    risk_data = classify_risk(
        days_remaining, stock_current, stock_in_transit,
        stock_min, stock_max, lead_time_avg, lead_time_std
    )

    total_replenishment_cost = formula_order_qty * unit_cost
    holding_cost_per_cycle   = (formula_order_qty / 2) * unit_cost * holding_cost_pct
    safety_stock_cost        = safety_stock * unit_cost

    high_cost_flag    = total_replenishment_cost > 50_000
    high_holding_flag = holding_cost_per_cycle > 10_000
    moq_is_binding    = (eoq > 0) and (eoq < moq)

    return {
        "stock": {
            "current_stock":      stock_current,
            "stock_in_transit":   stock_in_transit,
            "stock_min":          stock_min,
            "stock_max":          stock_max,
            "lifecycle_stage":    lifecycle_stage,
            # ── Product fields carried through so decision agent can
            #    re-run compute_inventory_metrics with uplift applied
            #    using real values instead of silent defaults. ──────────
            "lead_time_avg_days":    lead_time_avg,
            "lead_time_std_days":    lead_time_std,
            "unit_cost":             unit_cost,
            "holding_cost_pct":      holding_cost_pct,
            "order_cost":            order_cost,
            "service_level_target":  service_level_target,
        },
        "forecast": {
            "avg_daily_demand": avg_daily_demand,
            "demand_std_dev":   demand_std,
            "total_30d_demand": total_30d_demand,
            "trend_direction":  trend_direction,
            # Audit trail for the proration math above — lets a caller (or a
            # human reading the DB row) see exactly what was applied without
            # having to re-derive it from avg_daily_demand alone.
            "promo_uplift_pct_applied":     promo_uplift_pct,
            "impact_window_days_applied":   min(max(impact_window_days, 1.0), 30.0) if promo_uplift_pct != 0.0 else 0.0,
            "buffer_recommendation_pct_applied": max(0.0, buffer_recommendation_pct),
        },
        "metrics": {
            "days_of_stock_remaining":   days_remaining,
            "effective_service_level":   eff_sl,
            "service_level_explanation": sl_explanation,
            "z_score":                   z_score,
            "safety_stock":              safety_stock,
            "safety_stock_cost_dt":      safety_stock_cost,
            "reorder_point":             reorder_point,
            "eoq":                       eoq,
            "formula_order_qty":         formula_order_qty,  # NOT a recommendation
            "holding_cost_per_cycle_dt": holding_cost_per_cycle,
            "total_replenishment_cost":  total_replenishment_cost,
        },
        "risk_assessment": risk_data,
        "constraints": {
            "moq":             moq,
            "moq_is_binding":  moq_is_binding,
            "moq_binding_note": (
                f"EOQ ({eoq:.0f}) < MOQ ({moq:.0f}) — ordering more than optimal"
                if moq_is_binding else
                f"EOQ ({eoq:.0f}) ≥ MOQ ({moq:.0f}) — EOQ drives order size"
            ),
            "high_cost_flag":          high_cost_flag,
            "high_holding_flag":       high_holding_flag,
            "objective_conflict":      False,   # set by LLM in reason node
            "objective_conflict_note": None,
            "supplier_order_multiple": int(supplier_order_multiple or 1),
            "lot_size_rounded":        lot_size_rounded,  # True if rounding up actually changed the qty
        },
        "objective_note":   "",    # filled by LLM in reason node
        "analyst_flag":     None,  # filled by LLM in reason node
        "reasoning_source": "compute",  # updated by reason node
    }