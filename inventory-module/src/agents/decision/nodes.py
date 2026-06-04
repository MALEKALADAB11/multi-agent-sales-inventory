"""
Decision Agent Nodes
=====================
Single node: decide

One job: synthesise analysis_report + context_report + adjusted_metrics
into a concrete action and a single recommendation_text paragraph.

recommendation_text is written by the LLM as one coherent judgment.
The rule-based fallback builds the same structure deterministically —
same voice, same sentence order, no section headers or bullet points.
"""

import json
import logging
import re
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
    """Pull decision-critical fields from adjusted_metrics, falling back to baseline."""
    adj  = state.get("adjusted_metrics", {})
    base = state.get("baseline_report", {})

    adj_risk    = adj.get("risk_assessment") or base.get("risk_assessment", {})
    adj_metrics = adj.get("metrics") or base.get("metrics", {})
    base_stock  = base.get("stock", {})

    risk_level     = adj_risk.get("level") or (base.get("risk_assessment") or {}).get("level", "LOW")
    overstock_flag = (
        adj_risk.get("overstock_flag", False)
        or (base.get("risk_assessment") or {}).get("overstock_flag", False)
    )

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
    adj_m = (state.get("adjusted_metrics", {}).get("metrics") or base.get("metrics", {}))
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
        "obj_conflict":   cons.get("objective_conflict", False),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Fallback — same voice as LLM output
# ═══════════════════════════════════════════════════════════════════════════

def _build_recommendation_text(
    action:     str,
    urgency:    str,
    order_qty:  Optional[int],
    state:      Dict[str, Any],
    escalate:   bool,
    esc_reason: Optional[str],
) -> str:
    """
    Build recommendation_text in the same style the LLM uses:
    3-5 sentences, action first, numbers justify, context qualifies.
    No section headers, no bullet points, no filler.
    """
    base  = state.get("baseline_report", {})
    adj   = state.get("adjusted_metrics", {})
    ctx   = state.get("context_report", {})
    cons  = _extract_constraints(state)

    base_risk = base.get("risk_assessment", {})
    base_m    = base.get("metrics", {})
    adj_m     = adj.get("metrics") or base_m
    stock     = base.get("stock", {})

    adj_days  = _safe_float(adj_m.get("days_of_stock_remaining"),
                            _safe_float(base_m.get("days_of_stock_remaining")))
    base_days = _safe_float(base_m.get("days_of_stock_remaining"))
    rop       = _safe_float(adj_m.get("reorder_point"))
    adj_cost  = _safe_float(adj_m.get("total_replenishment_cost"))
    lead_time = cons["lead_time"]

    uplift   = _safe_float(ctx.get("demand_uplift_pct"))
    dominant = ctx.get("dominant_signal", "none")
    ctx_conf = ctx.get("confidence", "low")

    sentences = []

    # ── S1: What to do and when ───────────────────────────────────────────
    if action == "EXPEDITE":
        s1 = (
            f"Expedite {order_qty:,} units today — stock runs out in {adj_days:.0f} days "
            f"and the standard lead time is {lead_time:.0f} days, so a stockout is guaranteed "
            f"before a normal order arrives."
        )
    elif action == "ORDER":
        urgency_phrase = {"immediate": "today", "this_week": "this week",
                          "this_month": "this month"}.get(urgency, urgency)
        s1 = (
            f"Order {order_qty:,} units {urgency_phrase} — stock is at {adj_days:.0f} days "
            f"against a reorder point of {rop:.0f} units and a {lead_time:.0f}-day lead time."
        )
    elif action == "MONITOR":
        s1 = (
            f"No order yet — stock is at {adj_days:.0f} days, approaching but not past "
            f"the reorder point of {rop:.0f} units."
        )
    else:  # HOLD
        if base_risk.get("overstock_flag"):
            s1 = (
                f"Do not order — stock is above the maximum threshold at {adj_days:.0f} days, "
                f"adding more inventory would increase holding costs with no service benefit."
            )
        else:
            s1 = (
                f"No action needed — stock is at {adj_days:.0f} days, "
                f"well above the {rop:.0f}-unit reorder point."
            )

    sentences.append(s1)

    # ── S2-3: Why, in numbers ─────────────────────────────────────────────
    risk_level = base_risk.get("level", "")
    layer2     = base_risk.get("layer2_result", "")

    if action in ("ORDER", "EXPEDITE"):
        # Give the binding constraint
        if adj_days < lead_time:
            s2 = (
                f"At current demand, the shelf will be empty in {adj_days:.0f} days — "
                f"{lead_time:.0f} days before any standard replenishment could arrive."
            )
        else:
            s2 = (
                f"Stock dropped below the reorder point of {rop:.0f} units; "
                f"an order placed now will arrive with approximately "
                f"{max(0, adj_days - lead_time):.0f} days of buffer remaining."
            )
        sentences.append(s2)

        if adj_cost > 0:
            sentences.append(
                f"Replenishment cost is {adj_cost:,.0f} DT for {order_qty:,} units."
            )

    elif action == "MONITOR":
        sentences.append(
            f"Review again before stock drops below {rop:.0f} units — "
            f"if demand accelerates or lead time extends, move to ORDER."
        )

    else:  # HOLD
        sentences.append(
            f"Next review at the scheduled inventory check or if demand trend changes."
        )

    # ── S4: Context influence ─────────────────────────────────────────────
    if uplift != 0.0 and dominant != "none":
        day_delta = adj_days - base_days
        s_ctx = (
            f"A {uplift:+.1f}% demand uplift from {dominant} (confidence: {ctx_conf}) "
            f"was applied, reducing adjusted days from {base_days:.0f} to {adj_days:.0f}."
        )
        sentences.append(s_ctx)
    elif action in ("ORDER", "EXPEDITE") and dominant == "none":
        # Only mention lack of signals for ORDER/EXPEDITE where it's relevant
        sentences.append(
            f"No demand signals this week — the decision rests on inventory math alone."
        )

    # ── S5: Escalation flag ───────────────────────────────────────────────
    if escalate and esc_reason:
        sentences.append(f"Escalate to manager before placing: {esc_reason}")
    elif cons["obj_conflict"] and action in ("ORDER", "EXPEDITE"):
        sentences.append(
            "This order may conflict with the active business objective — "
            "review with the category manager."
        )

    return " ".join(sentences)


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Decision
# ═══════════════════════════════════════════════════════════════════════════

def _rule_based_decide(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic fallback. Same logic as the LLM prompt rules.
    Priority: overstock → EXPEDITE → ORDER → MONITOR → HOLD
    """
    (risk_level, days_remaining, formula_order_qty,
     reorder_point, repl_cost, overstock_flag, lead_time_avg) = _extract_adjusted(state)
    cons = _extract_constraints(state)

    lifecycle = cons["lifecycle"]
    override  = cons["risk_override"]

    # ── Action ───────────────────────────────────────────────────────────
    if overstock_flag:
        action, urgency = "HOLD", "none"
        rationale = (
            f"Stock exceeds the maximum threshold — adding more inventory "
            f"increases holding costs with no service benefit."
        )
    elif risk_level == "CRITICAL" and days_remaining < lead_time_avg:
        action, urgency = "EXPEDITE", "immediate"
        rationale = (
            f"Stock runs out in {days_remaining:.0f}d but replenishment takes "
            f"{lead_time_avg:.0f}d — stockout is guaranteed before standard order arrives."
        )
    elif risk_level in ("CRITICAL", "HIGH"):
        action  = "ORDER"
        urgency = "immediate" if risk_level == "CRITICAL" else "this_week"
        rationale = (
            f"{risk_level} risk: {days_remaining:.1f}d remaining against "
            f"{lead_time_avg:.0f}d lead time, reorder point {reorder_point:.0f} units."
        )
    elif risk_level == "MEDIUM":
        action, urgency = "MONITOR", "this_month"
        rationale = (
            f"MEDIUM risk: {days_remaining:.1f}d remaining, approaching reorder point "
            f"of {reorder_point:.0f} units but not yet there."
        )
    else:
        action, urgency = "HOLD", "none"
        rationale = f"LOW risk: {days_remaining:.1f}d of stock — no action required."

    order_qty = int(formula_order_qty) if action in ("ORDER", "EXPEDITE") else None

    # ── Trade-offs ───────────────────────────────────────────────────────
    if action in ("ORDER", "EXPEDITE"):
        trade_offs = (
            f"Ordering {formula_order_qty:.0f} units costs {repl_cost:,.0f} DT; "
            f"not ordering risks a stockout in {days_remaining:.0f}d."
        )
    elif action == "HOLD":
        trade_offs = "Holding preserves capital; monitor if demand spikes unexpectedly."
    else:
        trade_offs = "Monitoring delays commitment; re-evaluate if demand accelerates."

    # ── Escalation ───────────────────────────────────────────────────────
    escalate: bool            = False
    esc_reason: Optional[str] = None

    if action in ("ORDER", "EXPEDITE") and repl_cost > 50_000:
        escalate   = True
        esc_reason = f"Order cost {repl_cost:,.0f} DT exceeds 50,000 DT — human approval required."
    elif lifecycle in ("end_of_life", "discontinued") and risk_level == "CRITICAL":
        escalate   = True
        esc_reason = "CRITICAL on EOL/discontinued product — verify intentional drawdown before ordering."
    elif override == "ESCALATE":
        escalate   = True
        esc_reason = "Analysis agent flagged a cross-dimensional conflict — human review required."
    elif action in ("ORDER", "EXPEDITE") and cons["high_cost_flag"] and cons["obj_conflict"]:
        escalate   = True
        esc_reason = "High-cost order conflicts with active business objective — sign-off needed."

    # ── Confidence ───────────────────────────────────────────────────────
    ctx          = state.get("context_report", {})
    uplift       = _safe_float(ctx.get("demand_uplift_pct"))
    ctx_conf     = ctx.get("confidence", "low")

    if override or cons["obj_conflict"] or cons["moq_is_binding"]:
        # Structural uncertainty — always low regardless of risk clarity
        confidence = "low"
    elif uplift != 0.0 and ctx_conf == "low":
        # Signal was applied but is uncertain — medium even if risk is clear
        confidence = "medium"
    elif uplift == 0.0 and ctx_conf == "low":
        # No signal detected — decision rests on math alone, which is valid.
        # Do not penalise for context finding nothing. Trust the risk level.
        confidence = "high" if risk_level in ("CRITICAL", "LOW") else "medium"
    elif risk_level in ("CRITICAL", "LOW") and not escalate:
        confidence = "high"
    else:
        confidence = "medium"

    recommendation_text = _build_recommendation_text(
        action=action,
        urgency=urgency,
        order_qty=order_qty,
        state=state,
        escalate=escalate,
        esc_reason=esc_reason,
    )

    return {
        "action":               action,
        "order_qty":            order_qty,
        "order_qty_rationale":  f"max(EOQ={formula_order_qty:.0f}, MOQ={cons['moq']:.0f}) adjusted for demand uplift.",
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
            context_interpretation      = ctx.get("interpretation", "No demand signals detected."),
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

            # Strip markdown fences
            if raw.startswith("```"):
                parts = raw.split("```")
                raw   = parts[1] if len(parts) > 1 else parts[0]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            # Fix control characters that break json.loads
            # (LLM sometimes embeds literal newlines inside JSON string values)
            raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Last resort: collapse all literal newlines inside strings
                raw    = re.sub(r'\n', ' ', raw)
                parsed = json.loads(raw)

            raw_qty = parsed.get("order_qty")
            try:
                order_qty = int(raw_qty) if raw_qty is not None else None
            except (TypeError, ValueError):
                order_qty = None

            # Validate recommendation_text — if LLM omitted or emptied it,
            # build it from the rule-based fallback using the LLM's action choice
            recommendation_text = str(parsed.get("recommendation_text", "")).strip()
            if not recommendation_text:
                logger.warning(
                    "[Decision/decide] LLM omitted recommendation_text for SKU=%s "
                    "— building from rule-based", sku,
                )
                # Construct a minimal state subset to feed the fallback builder
                recommendation_text = _build_recommendation_text(
                    action    = str(parsed.get("action", "MONITOR")).upper(),
                    urgency   = str(parsed.get("urgency", "none")),
                    order_qty = order_qty,
                    state     = state,
                    escalate  = bool(parsed.get("escalate_to_human", False)),
                    esc_reason= parsed.get("escalation_reason"),
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