"""
Decision Agent Nodes
=====================
Single node: decide

The LLM produces recommendation_text as part of its JSON output — it writes
a human-readable recommendation tailored to the specific situation.

The rule-based fallback constructs recommendation_text deterministically from
available fields. It will be more structured and less contextual than the LLM
version, but still complete and operator-readable.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract_adjusted(state: Dict[str, Any]) -> tuple:
    """
    Pull the fields the decide logic needs from adjusted_metrics.
    adjusted_metrics may be the full compute_inventory_metrics() output
    (has sub-dicts) or a flat fallback copy of baseline keys.
    """
    adj  = state.get("adjusted_metrics", {})
    base = state.get("baseline_report", {})

    adj_risk    = adj.get("risk_assessment") or base.get("risk_assessment", {})
    adj_metrics = adj.get("metrics") or base.get("metrics", {})
    base_stock  = base.get("stock", {})

    risk_level     = adj_risk.get("level") or (base.get("risk_assessment") or {}).get("level", "LOW")
    overstock_flag = adj_risk.get("overstock_flag", False) or \
                     (base.get("risk_assessment") or {}).get("overstock_flag", False)

    days_remaining    = _safe_float(adj_metrics.get("days_of_stock_remaining"), 999.0)
    formula_order_qty = _safe_float(adj_metrics.get("formula_order_qty"), 0.0)
    reorder_point     = _safe_float(adj_metrics.get("reorder_point"), 0.0)
    repl_cost         = _safe_float(adj_metrics.get("total_replenishment_cost"), 0.0)
    lead_time_avg     = _safe_float(base_stock.get("lead_time_avg_days"), 14.0)

    return (risk_level, days_remaining, formula_order_qty,
            reorder_point, repl_cost, overstock_flag, lead_time_avg)


def _extract_constraints(state: Dict[str, Any]) -> Dict[str, Any]:
    base  = state.get("baseline_report", {})
    cons  = base.get("constraints", {})
    stock = base.get("stock", {})
    adj_m = (state.get("adjusted_metrics", {}).get("metrics") or
             base.get("metrics", {}))
    return {
        "moq":            _safe_float(cons.get("moq"), 1.0),
        "moq_is_binding": cons.get("moq_is_binding", False),
        "high_cost_flag": cons.get("high_cost_flag", False),
        "obj_conflict":   cons.get("objective_conflict", False),
        "lifecycle":      stock.get("lifecycle_stage", "mature"),
        "lead_time":      _safe_float(stock.get("lead_time_avg_days"), 14.0),
        "risk_override":  (base.get("risk_assessment") or {}).get("override"),
        "repl_cost":      _safe_float(adj_m.get("total_replenishment_cost"), 0.0),
        "analyst_flag":   base.get("analyst_flag", ""),
        "risk_rationale": (base.get("risk_assessment") or {}).get("rationale", ""),
        "obj_note":       base.get("objective_note", ""),
        "override_reason":(base.get("risk_assessment") or {}).get("override_reason", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Fallback — recommendation_text builder
# ═══════════════════════════════════════════════════════════════════════════

def _build_fallback_recommendation_text(
    action:      str,
    urgency:     str,
    order_qty:   Optional[int],
    confidence:  str,
    trade_offs:  str,
    escalate:    bool,
    esc_reason:  Optional[str],
    state:       Dict[str, Any],
) -> str:
    """
    Construct a human-readable recommendation_text from available fields.
    Called only by the rule-based fallback path — the LLM writes its own.
    Each section only appears if it has real content to add.
    """
    base  = state.get("baseline_report", {})
    adj   = state.get("adjusted_metrics", {})
    ctx   = state.get("context_report", {})
    cons  = _extract_constraints(state)

    base_risk = base.get("risk_assessment", {})
    base_m    = base.get("metrics", {})
    adj_m     = adj.get("metrics") or base_m
    stock     = base.get("stock", {})
    sku       = state.get("sku", "?")
    store_id  = state.get("store_id", "?")

    risk_level      = base_risk.get("level", "?")
    risk_override   = base_risk.get("override")
    override_reason = base_risk.get("override_reason", "")
    risk_rationale  = base_risk.get("rationale", "")
    overstock_flag  = base_risk.get("overstock_flag", False)
    lifecycle       = stock.get("lifecycle_stage", "")

    base_days = _safe_float(base_m.get("days_of_stock_remaining"))
    adj_days  = _safe_float(adj_m.get("days_of_stock_remaining"), base_days)
    rop       = _safe_float(adj_m.get("reorder_point"))
    adj_cost  = _safe_float(adj_m.get("total_replenishment_cost"))
    lead_time = cons["lead_time"]
    moq       = cons["moq"]
    moq_binding = cons["moq_is_binding"]

    uplift    = _safe_float(ctx.get("demand_uplift_pct"))
    dominant  = ctx.get("dominant_signal", "none")
    ctx_conf  = ctx.get("confidence", "low")
    ctx_interp = ctx.get("interpretation", "")

    urgency_label = {
        "immediate":  "IMMEDIATE — act today",
        "this_week":  "this week",
        "this_month": "this month",
        "none":       "no urgency",
    }.get(urgency, urgency)

    sections = []

    # SITUATION
    situation = (
        f"SKU {sku} at store {store_id} has {adj_days:.1f} days of stock remaining"
        f" (lifecycle: {lifecycle}). Risk level: {risk_level}"
    )
    if risk_override:
        situation += f", {risk_override} by LLM evaluator ({override_reason})"
    situation += "."
    if overstock_flag:
        situation += " Stock exceeds the maximum threshold — overstock flag is set."
    if risk_rationale:
        situation += f" {risk_rationale}"
    sections.append("SITUATION\n  " + situation)

    # SIGNALS — only if something meaningful
    if uplift != 0.0 or (dominant != "none" and dominant):
        delta = adj_days - base_days
        sig = (
            f"Context agent applied a {uplift:+.1f}% demand uplift "
            f"(dominant signal: {dominant}, confidence: {ctx_conf})."
        )
        if ctx_interp:
            first = ctx_interp.split(".")[0].strip()
            if first:
                sig += f" {first}."
        if abs(delta) > 0.5:
            sig += f" Adjusted days of stock: {adj_days:.1f}d (was {base_days:.1f}d baseline)."
        sections.append("SIGNALS\n  " + sig)

    # ACTION
    if action in ("ORDER", "EXPEDITE"):
        act = f"{action} — {urgency_label}. "
        act += f"Order {order_qty:,} units"
        if moq:
            act += f" (MOQ: {moq:.0f}"
            if moq_binding:
                act += " — binding"
            act += ")"
        act += f" at an estimated cost of {adj_cost:,.0f} DT."
        act += f" Reorder point: {rop:.0f} units. Lead time: {lead_time:.0f} days."
        if cons["high_cost_flag"]:
            act += " High-cost order flag is set (>50,000 DT)."
        act += f" Decision confidence: {confidence} (rule-based fallback)."
    elif action == "MONITOR":
        act = (
            f"MONITOR — no order required yet. "
            f"Stock is at {adj_days:.1f}d, approaching the reorder point of {rop:.0f} units. "
            f"Check again before stock drops further."
        )
    else:  # HOLD
        act = (
            f"HOLD — no order required. "
            f"Stock is sufficient at {adj_days:.1f}d remaining."
        )
    sections.append("ACTION\n  " + act)

    # TRADE-OFFS
    if trade_offs:
        sections.append("TRADE-OFFS\n  " + trade_offs)

    # FLAGS — only if something to report
    flags = []
    if cons["analyst_flag"]:
        flags.append(f"Analyst flag: {cons['analyst_flag']}")
    if risk_override:
        flags.append(f"Risk override: {risk_override} — {override_reason}")
    if cons["obj_conflict"]:
        flags.append("Objective conflict detected — this recommendation may conflict with the active business objective.")
    if escalate and esc_reason:
        flags.append(f"⚠ Escalate to human: {esc_reason}")
    if flags:
        sections.append("FLAGS\n  " + "\n  ".join(flags))

    return "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Decision
# ═══════════════════════════════════════════════════════════════════════════

def _rule_based_decide(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic decision tree used when use_llm=False or LLM errors.

    Priority:
      1. overstock_flag=True                     → HOLD
      2. CRITICAL + days < lead_time             → EXPEDITE (imminent)
      3. CRITICAL or HIGH                        → ORDER
      4. MEDIUM                                  → MONITOR
      5. LOW                                     → HOLD
    """
    (risk_level, days_remaining, formula_order_qty,
     reorder_point, repl_cost, overstock_flag, lead_time_avg) = _extract_adjusted(state)
    cons = _extract_constraints(state)

    lifecycle = cons["lifecycle"]
    override  = cons["risk_override"]

    # ── Action ───────────────────────────────────────────────────────────
    if overstock_flag:
        action   = "HOLD"
        urgency  = "none"
        rationale = (
            f"Stock exceeds the maximum threshold. Adding more inventory "
            f"would increase holding costs with no service benefit."
        )
    elif risk_level == "CRITICAL" and days_remaining < lead_time_avg:
        action   = "EXPEDITE"
        urgency  = "immediate"
        rationale = (
            f"Stock will run out in {days_remaining:.0f}d but replenishment takes "
            f"{lead_time_avg:.0f}d — a stockout is imminent. "
            f"Use express shipping or an alternate supplier."
        )
    elif risk_level in ("CRITICAL", "HIGH"):
        action   = "ORDER"
        urgency  = "immediate" if risk_level == "CRITICAL" else "this_week"
        rationale = (
            f"{risk_level} risk: {days_remaining:.1f}d of stock remaining, "
            f"reorder point is {reorder_point:.0f} units. "
            f"Place replenishment order of {formula_order_qty:.0f} units."
        )
    elif risk_level == "MEDIUM":
        action   = "MONITOR"
        urgency  = "this_month"
        rationale = (
            f"MEDIUM risk: {days_remaining:.1f}d remaining, approaching reorder point. "
            f"No order required yet — review before the reorder window closes."
        )
    else:
        action   = "HOLD"
        urgency  = "none"
        rationale = f"LOW risk: {days_remaining:.1f}d of stock. No action required."

    order_qty = int(formula_order_qty) if action in ("ORDER", "EXPEDITE") else None

    # ── Trade-offs ───────────────────────────────────────────────────────
    if action in ("ORDER", "EXPEDITE"):
        trade_offs = (
            f"Ordering {formula_order_qty:.0f} units costs {repl_cost:,.0f} DT. "
            f"Not ordering risks a stockout in ~{days_remaining:.0f}d."
        )
    elif action == "HOLD":
        trade_offs = "Holding preserves capital but may require expediting if demand spikes unexpectedly."
    else:
        trade_offs = "Monitoring delays commitment but risks missing the reorder window if demand accelerates."

    # ── Escalation ───────────────────────────────────────────────────────
    escalate: bool          = False
    esc_reason: Optional[str] = None

    if action in ("ORDER", "EXPEDITE") and repl_cost > 50_000:
        escalate   = True
        esc_reason = f"Order cost {repl_cost:,.0f} DT exceeds 50,000 DT threshold — human approval required."
    elif lifecycle in ("end_of_life", "discontinued") and risk_level == "CRITICAL":
        escalate   = True
        esc_reason = "CRITICAL risk on EOL/discontinued product — verify intentional drawdown vs real stockout before ordering."
    elif override == "ESCALATE":
        escalate   = True
        esc_reason = "Analysis agent LLM flagged an unusual cross-dimensional conflict — human review required."
    elif action in ("ORDER", "EXPEDITE") and cons["high_cost_flag"] and cons["obj_conflict"]:
        escalate   = True
        esc_reason = "High-cost order conflicts with active business objective — human sign-off needed."

    # ── Confidence ───────────────────────────────────────────────────────
    if risk_level in ("CRITICAL", "LOW") and not escalate:
        confidence = "high"
    elif override or cons["obj_conflict"] or cons["moq_is_binding"]:
        confidence = "low"
    else:
        confidence = "medium"

    # ── Recommendation text — rule-based version ─────────────────────────
    recommendation_text = _build_fallback_recommendation_text(
        action=action,
        urgency=urgency,
        order_qty=order_qty,
        confidence=confidence,
        trade_offs=trade_offs,
        escalate=escalate,
        esc_reason=esc_reason,
        state=state,
    )

    return {
        "action":               action,
        "order_qty":            order_qty,
        "order_qty_rationale":  "max(EOQ, MOQ) adjusted for context demand uplift.",
        "urgency":              urgency,
        "decision_rationale":   rationale,
        "confidence":           confidence,
        "trade_offs":           trade_offs,
        "escalate_to_human":    escalate,
        "escalation_reason":    esc_reason,
        "recommendation_text":  recommendation_text,
        "reasoning_source":     "rule_based_fallback",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Node Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_decide_node(llm, use_llm: bool):
    """
    Returns the decide node bound to the given LLM.
    Falls back to rule-based if use_llm=False, llm is None, or LLM errors.
    """

    def decide_node(state: Dict[str, Any]) -> Dict[str, Any]:
        sku      = state["sku"]
        store_id = state["store_id"]

        if not use_llm or llm is None:
            return {**state, "decision": _rule_based_decide(state)}

        # ── LLM path ──────────────────────────────────────────────────────
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.agents.decision.prompts import DECIDE_SYSTEM, DECIDE_USER

        base    = state["baseline_report"]
        adj     = state["adjusted_metrics"]
        ctx     = state["context_report"]
        obj     = state["business_objective"]

        b_metrics = base.get("metrics", {})
        b_risk    = base.get("risk_assessment", {})
        a_metrics = adj.get("metrics") or base.get("metrics", {})
        a_risk    = adj.get("risk_assessment") or base.get("risk_assessment", {})
        cons      = base.get("constraints", {})
        stock     = base.get("stock", {})

        user_prompt = DECIDE_USER.format(
            sku=sku,
            store_id=store_id,
            business_objective=obj,
            # Analysis
            baseline_risk_level         = b_risk.get("level", "N/A"),
            risk_override               = b_risk.get("override") or "none",
            override_reason             = b_risk.get("override_reason") or "none",
            overstock_flag              = b_risk.get("overstock_flag", False),
            baseline_days_remaining     = _safe_float(b_metrics.get("days_of_stock_remaining")),
            baseline_formula_order_qty  = _safe_float(b_metrics.get("formula_order_qty")),
            baseline_reorder_point      = _safe_float(b_metrics.get("reorder_point")),
            baseline_replenishment_cost = _safe_float(b_metrics.get("total_replenishment_cost")),
            risk_rationale              = b_risk.get("rationale", ""),
            objective_note              = base.get("objective_note", ""),
            analyst_flag                = base.get("analyst_flag") or "none",
            objective_conflict          = cons.get("objective_conflict", False),
            lifecycle_stage             = stock.get("lifecycle_stage", "mature"),
            lead_time_avg               = _safe_float(stock.get("lead_time_avg_days"), 14.0),
            lead_time_std               = _safe_float(stock.get("lead_time_std_days"), 3.0),
            moq                         = _safe_float(cons.get("moq"), 1.0),
            moq_is_binding              = cons.get("moq_is_binding", False),
            high_cost_flag              = cons.get("high_cost_flag", False),
            # Context
            demand_uplift_pct           = _safe_float(ctx.get("demand_uplift_pct")),
            dominant_signal             = ctx.get("dominant_signal", "none"),
            context_confidence          = ctx.get("confidence", "low"),
            context_interpretation      = ctx.get("interpretation", "no context signals"),
            # Adjusted
            adjusted_risk_level         = a_risk.get("level", "N/A"),
            adjusted_days_remaining     = _safe_float(a_metrics.get("days_of_stock_remaining")),
            adjusted_formula_order_qty  = _safe_float(a_metrics.get("formula_order_qty")),
            adjusted_reorder_point      = _safe_float(a_metrics.get("reorder_point")),
            adjusted_replenishment_cost = _safe_float(a_metrics.get("total_replenishment_cost")),
            adjusted_safety_stock       = _safe_float(a_metrics.get("safety_stock")),
        )

        try:
            response = llm.invoke([
                SystemMessage(content=DECIDE_SYSTEM),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content.strip()

            if raw.startswith("```"):
                parts = raw.split("```")
                raw   = parts[1] if len(parts) > 1 else parts[0]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            parsed = json.loads(raw)

            # Sanitise order_qty
            raw_qty = parsed.get("order_qty")
            try:
                order_qty = int(raw_qty) if raw_qty is not None else None
            except (TypeError, ValueError):
                order_qty = None

            # recommendation_text: use LLM's version; fall back to rule-based builder
            # if the LLM omitted it or produced an empty string
            recommendation_text = str(parsed.get("recommendation_text", "")).strip()
            if not recommendation_text:
                logger.warning(
                    "[Decision/decide] LLM omitted recommendation_text for SKU=%s — "
                    "using rule-based fallback text",
                    sku,
                )
                # Build a partial decision dict so the fallback builder has something to work with
                _partial = {
                    "action":    str(parsed.get("action", "MONITOR")).upper(),
                    "urgency":   str(parsed.get("urgency", "none")),
                    "order_qty": order_qty,
                    "confidence": str(parsed.get("confidence", "low")),
                    "trade_offs": str(parsed.get("trade_offs", "")),
                    "escalate_to_human": bool(parsed.get("escalate_to_human", False)),
                    "escalation_reason": parsed.get("escalation_reason"),
                }
                recommendation_text = _build_fallback_recommendation_text(
                    action=_partial["action"],
                    urgency=_partial["urgency"],
                    order_qty=_partial["order_qty"],
                    confidence=_partial["confidence"],
                    trade_offs=_partial["trade_offs"],
                    escalate=_partial["escalate_to_human"],
                    esc_reason=_partial["escalation_reason"],
                    state=state,
                )

            decision = {
                "action":               str(parsed.get("action", "MONITOR")).upper(),
                "order_qty":            order_qty,
                "order_qty_rationale":  str(parsed.get("order_qty_rationale", "")),
                "urgency":              str(parsed.get("urgency", "none")),
                "decision_rationale":   str(parsed.get("decision_rationale", "")),
                "confidence":           str(parsed.get("confidence", "low")),
                "trade_offs":           str(parsed.get("trade_offs", "")),
                "escalate_to_human":    bool(parsed.get("escalate_to_human", False)),
                "escalation_reason":    parsed.get("escalation_reason"),
                "recommendation_text":  recommendation_text,
                "reasoning_source":     "llm",
            }

            logger.info(
                "[Decision/decide] SKU=%s action=%s qty=%s urgency=%s confidence=%s",
                sku, decision["action"], decision["order_qty"],
                decision["urgency"], decision["confidence"],
            )

        except Exception as e:
            logger.warning(
                "[Decision/decide] LLM failed for SKU=%s: %s — using rule-based fallback",
                sku, e,
            )
            decision = _rule_based_decide(state)

        return {**state, "decision": decision}

    return decide_node