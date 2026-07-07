"""
Critic / Guardrail Agent
========================
Validates any coach recommendation before it reaches the frontend.

Seven guardrail rules (from ARCHITECTURE_AGENTIQUE_COMMUNICATION.md §9):
  G1 Stock      — Never recommend an unavailable product
  G2 Rupture    — Avoid pushing imminent-stockout products unless clearance
  G3 RAG        — Commercial arguments must come from a reliable source
  G4 Business   — No unauthorized discounts or offers
  G5 Network    — No 5G/Fibre recommendation if client not eligible
  G6 Confidence — If confidence < 0.65, rewrite or escalate
  G7 Budget     — Large orders (>100k DT) require manager approval

Possible outcomes:
  APPROVE   → send to frontend as-is
  REWRITE   → return to CoachAgent with specific feedback
  ESCALATE  → route to HITL (manager validation)
  BLOCK     → replace with safe fallback, do NOT send original

Usage:
    from app.sales.coaching.agents.guardrail.guardrail_agent import evaluate_guardrails

    result = evaluate_guardrails(
        recommendation=coach_output_dict,
        store_id="I63",
        inventory_snapshot=snapshot_dict,
        rag_used=True,
        confidence=0.88,
    )
    # result["status"]   → "APPROVE" | "REWRITE" | "ESCALATE" | "BLOCK"
    # result["issues"]   → list of violated rules
    # result["feedback"] → instruction for CoachAgent rewrite (when REWRITE)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Thresholds (overridable via env) ────────────────────────────────────────

_CONFIDENCE_MIN   = float(os.getenv("GUARDRAIL_CONFIDENCE_MIN",   "0.65"))
_BUDGET_CAP_DT    = float(os.getenv("GUARDRAIL_BUDGET_CAP_DT",    "100000"))
_STOCKOUT_DAYS    = float(os.getenv("GUARDRAIL_STOCKOUT_DAYS",     "3.0"))


# ═══════════════════════════════════════════════════════════════════════════════
# Individual Guardrail Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _g1_stock_available(
    product_to_push: Optional[str],
    inventory_snapshot: Dict[str, Any],
) -> Optional[str]:
    """G1 — Never recommend an unavailable product (stock = 0)."""
    if not product_to_push or not inventory_snapshot:
        return None
    skus = inventory_snapshot.get("skus", {})
    for sku, info in skus.items():
        name = info.get("product_name", "").lower()
        if product_to_push.lower() in name or product_to_push.lower() in sku.lower():
            if int(info.get("stock_qty", 1)) == 0:
                return (
                    f"G1: '{product_to_push}' has zero stock (SKU {sku}). "
                    f"Cannot recommend an unavailable product."
                )
    return None


def _g2_stockout_imminent(
    product_to_push: Optional[str],
    inventory_snapshot: Dict[str, Any],
    is_clearance: bool = False,
) -> Optional[str]:
    """G2 — Avoid pushing products with imminent stockout (< GUARDRAIL_STOCKOUT_DAYS days)."""
    if not product_to_push or not inventory_snapshot or is_clearance:
        return None
    skus = inventory_snapshot.get("skus", {})
    for sku, info in skus.items():
        name = info.get("product_name", "").lower()
        if product_to_push.lower() in name or product_to_push.lower() in sku.lower():
            days_left = float(info.get("days_remaining", 999))
            if days_left < _STOCKOUT_DAYS:
                return (
                    f"G2: '{product_to_push}' has only {days_left:.1f}d of stock left — "
                    f"imminent stockout. Recommend an alternative unless this is a clearance push."
                )
    return None


def _g3_rag_source(
    rag_used: bool,
    confidence: float,
    nb_scripts: int = 0,
) -> Optional[str]:
    """G3 — Commercial arguments should come from a RAG-verified source when gap > 20%."""
    if not rag_used and nb_scripts == 0 and confidence < 0.7:
        return (
            "G3: Recommendation generated without RAG scripts and low confidence "
            f"({confidence:.2f}). Arguments may not be grounded in Ooredoo-approved playbooks."
        )
    return None


def _g4_business_rules(
    message: str,
    strategy_actions: List[Dict[str, Any]],
) -> Optional[str]:
    """G4 — Detect unauthorized discount or offer patterns in the output."""
    forbidden_patterns = [
        "remise de ", "réduction de ", "offrir gratuitement",
        "50% off", "cadeau inclus sans", "hors catalogue",
    ]
    text = (message + " ".join(a.get("argument_vente", "") for a in strategy_actions)).lower()
    for pattern in forbidden_patterns:
        if pattern in text:
            return (
                f"G4: Detected unauthorized offer pattern '{pattern}' in recommendation. "
                f"Only approved Ooredoo promotions may be communicated."
            )
    return None


def _g5_network_eligibility(
    message: str,
    product_to_push: Optional[str],
) -> Optional[str]:
    """G5 — Don't recommend 5G/Fibre if the message doesn't confirm eligibility."""
    high_tech_products = ["5g", "fibre", "fiber", "ftth"]
    msg_lower = (message + (product_to_push or "")).lower()
    for product in high_tech_products:
        if product in msg_lower:
            eligibility_keywords = ["éligible", "eligible", "couvert", "zone", "testé"]
            if not any(kw in msg_lower for kw in eligibility_keywords):
                return (
                    f"G5: Recommendation includes '{product}' offer but no eligibility "
                    f"verification was mentioned. Verify coverage before pushing."
                )
    return None


def _g6_confidence(confidence: float) -> Optional[str]:
    """G6 — If confidence < threshold, flag for rewrite or escalation."""
    if confidence < _CONFIDENCE_MIN:
        return (
            f"G6: Confidence score {confidence:.2f} is below minimum {_CONFIDENCE_MIN:.2f}. "
            f"Recommendation should be rewritten or escalated for human review."
        )
    return None


def _g7_budget(
    order_cost: float,
    action: Optional[str],
) -> Optional[str]:
    """G7 — Large inventory orders need manager approval."""
    if action in ("ORDER", "EXPEDITE") and order_cost > _BUDGET_CAP_DT:
        return (
            f"G7: Inventory order cost {order_cost:,.0f} DT exceeds budget cap "
            f"{_BUDGET_CAP_DT:,.0f} DT. Manager approval required before executing."
        )
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Routing Logic
# ═══════════════════════════════════════════════════════════════════════════════

# Severity of each guardrail when violated
_GUARDRAIL_SEVERITY: Dict[str, str] = {
    "G1": "BLOCK",     # zero-stock product → hard block
    "G2": "REWRITE",   # imminent stockout → rewrite with alternative
    "G3": "REWRITE",   # low-confidence + no RAG → rewrite
    "G4": "BLOCK",     # unauthorized offer → hard block
    "G5": "REWRITE",   # no eligibility check → rewrite
    "G6": "ESCALATE",  # low confidence → human review
    "G7": "ESCALATE",  # budget exceeded → manager approval
}

_SEVERITY_RANK = {"BLOCK": 3, "ESCALATE": 2, "REWRITE": 1, "APPROVE": 0}


def _compute_status(issues: List[Dict[str, str]]) -> str:
    """Pick the highest-severity status across all violated guardrails."""
    if not issues:
        return "APPROVE"
    return max(
        (_GUARDRAIL_SEVERITY.get(i["rule"], "REWRITE") for i in issues),
        key=lambda s: _SEVERITY_RANK.get(s, 0),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_guardrails(
    recommendation:     Dict[str, Any],
    store_id:           str,
    inventory_snapshot: Dict[str, Any] | None = None,
    rag_used:           bool = False,
    nb_scripts:         int  = 0,
    confidence:         float = 0.9,
    inventory_decision: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Run all 7 guardrails against a coach recommendation.

    Args:
        recommendation:     CoachAgent output dict — must contain at least
                            'message_for_advisor' and optionally 'product_to_push',
                            'product_to_avoid', 'strategy_actions'.
        store_id:           Store identifier.
        inventory_snapshot: Output of get_inventory_snapshot() — SKU stocks.
        rag_used:           Whether RAG retrieved scripts for this cycle.
        nb_scripts:         Number of RAG scripts retrieved.
        confidence:         Overall confidence score [0.0 – 1.0].
        inventory_decision: Latest InventoryDecisionAgent output (for G7 budget check).

    Returns:
        {
          "status":           "APPROVE" | "REWRITE" | "ESCALATE" | "BLOCK",
          "issues":           [ {"rule": "G1", "message": "..."}, ... ],
          "feedback":         "Instruction for CoachAgent rewrite (when REWRITE)",
          "final_confidence": float,
          "requires_human_validation": bool,
          "safe_fallback":    str  (when BLOCK)
        }
    """
    product_to_push   = recommendation.get("product_to_push")
    product_to_avoid  = recommendation.get("product_to_avoid")
    message           = recommendation.get("message_for_advisor", "")
    strategy_actions  = recommendation.get("strategy_actions", [])
    is_clearance      = "écoulement" in message.lower() or "clearance" in message.lower()

    inv = inventory_snapshot or {}
    inv_decision = inventory_decision or {}
    order_cost   = float(inv_decision.get("repl_cost", 0))
    inv_action   = inv_decision.get("action")

    # Run all checks
    raw_issues: List[Optional[str]] = [
        ("G1", _g1_stock_available(product_to_push, inv)),
        ("G2", _g2_stockout_imminent(product_to_push, inv, is_clearance)),
        ("G3", _g3_rag_source(rag_used, confidence, nb_scripts)),
        ("G4", _g4_business_rules(message, strategy_actions)),
        ("G5", _g5_network_eligibility(message, product_to_push)),
        ("G6", _g6_confidence(confidence)),
        ("G7", _g7_budget(order_cost, inv_action)),
    ]

    issues = [
        {"rule": rule, "message": msg}
        for rule, msg in raw_issues
        if msg is not None
    ]

    status = _compute_status(issues)

    # Build feedback for REWRITE
    feedback = ""
    if status == "REWRITE":
        rewrite_items = [i["message"] for i in issues if _GUARDRAIL_SEVERITY.get(i["rule"]) == "REWRITE"]
        feedback = (
            "Coach: please revise your recommendation addressing these issues:\n"
            + "\n".join(f"• {m}" for m in rewrite_items)
        )

    # Build safe fallback for BLOCK
    safe_fallback = ""
    if status == "BLOCK":
        safe_fallback = (
            "Je vous recommande de consulter notre catalogue produits disponibles "
            "et de contacter le manager pour valider l'action commerciale appropriée."
        )

    result = {
        "status":                    status,
        "issues":                    issues,
        "feedback":                  feedback,
        "final_confidence":          round(confidence * (0.5 if issues else 1.0), 3),
        "requires_human_validation": status in ("ESCALATE", "BLOCK"),
        "safe_fallback":             safe_fallback,
    }

    if issues:
        logger.warning(
            "[Guardrail] %s — %d issue(s) for %s: %s",
            status, len(issues), store_id,
            "; ".join(i["rule"] for i in issues),
        )
    else:
        logger.info("[Guardrail] APPROVE — %s (confidence=%.2f)", store_id, confidence)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# LangGraph Node Wrapper
# ═══════════════════════════════════════════════════════════════════════════════

def guardrail_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph-compatible node.
    Reads from RetailState, writes guardrail_status + guardrail_issues back.
    """
    recommendation = {
        "product_to_push":  state.get("focus_produits", [None])[0] if state.get("focus_produits") else None,
        "product_to_avoid": None,
        "message_for_advisor": state.get("message_manager", "") or state.get("strategie", ""),
        "strategy_actions": state.get("strategie_actions", []),
    }

    inv_snapshot = state.get("inventory_snapshot") or {}
    # Adapt inventory_snapshot to expected format
    if inv_snapshot and "nb_ruptures" in inv_snapshot:
        inv_snapshot = {"skus": {}, "critical_count": inv_snapshot.get("nb_critical_stock", 0)}

    result = evaluate_guardrails(
        recommendation     = recommendation,
        store_id           = state.get("store_id", ""),
        inventory_snapshot = inv_snapshot,
        rag_used           = state.get("rag_used", False),
        nb_scripts         = state.get("nb_rag_scripts", 0),
        confidence         = float(state.get("critique_score", 0.9)),
        inventory_decision = {},
    )

    return {
        **state,
        "guardrail_status":          result["status"],
        "guardrail_issues":          result["issues"],
        "guardrail_feedback":        result["feedback"],
        "guardrail_confidence":      result["final_confidence"],
        "requires_human_validation": result["requires_human_validation"],
        "guardrail_safe_fallback":   result["safe_fallback"],
    }


def route_guardrail(state: Dict[str, Any]) -> str:
    """Conditional edge router for LangGraph after guardrail_node."""
    return state.get("guardrail_status", "APPROVE").lower()
