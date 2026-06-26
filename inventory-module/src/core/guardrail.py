"""
Critic / Guardrail Agent — validation avant publication frontend.

Pattern : Self-critique (Reflexion) appliqué à la recommandation finale.

Checks métier :
  1. conseil_personnalise ≥ 50 chars         → sinon REWRITE
  2. ORDER avec qty ≤ 0                       → sinon REWRITE
  3. escalate_to_human dans décision stock     → ESCALATE
  4. Risk CRITICAL + action HOLD/MONITOR      → REWRITE (incohérence)
  5. Urgence HIGH/CRITICAL sans produit_a_pousser → REWRITE
  6. Confidence < 0.3 sur SKU CRITICAL        → ESCALATE

Verdict :
  APPROVE  — tout valide, publier vers le frontend
  REWRITE  — incohérence détectée, CoachAgent doit reformuler (max 2 cycles)
  ESCALATE — validation manager requise (Human-in-the-Loop)
  BLOCK    — contradiction critique non corrigeable

Le node incrémente guardrail_cycles à chaque appel pour éviter les boucles.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from .state import RetailState


# ── Checks individuels ────────────────────────────────────────────────────────

def _check_conseil_length(state: RetailState) -> tuple[bool, str]:
    conseil = (state.get("conseil_personnalise") or "").strip()
    if len(conseil) < 50:
        return False, f"conseil_personnalise trop court ({len(conseil)} chars < 50)"
    return True, ""


def _check_order_qty(state: RetailState) -> tuple[bool, str]:
    for dec in state.get("inventory_decisions", []):
        if dec.get("action") == "ORDER":
            qty = dec.get("order_qty") or 0
            if int(qty) <= 0:
                return False, f"ORDER qty={qty} ≤ 0 pour SKU {dec.get('sku', '?')}"
    return True, ""


def _check_escalation_flag(state: RetailState) -> tuple[bool, str]:
    for dec in state.get("inventory_decisions", []):
        if dec.get("escalate_to_human"):
            return False, (
                f"escalate_to_human=True pour SKU {dec.get('sku', '?')} "
                f"(raison: {dec.get('escalation_reason', 'non précisée')})"
            )
    return True, ""


def _check_critical_action_coherence(state: RetailState) -> tuple[bool, str]:
    for dec in state.get("inventory_decisions", []):
        if (
            dec.get("risk_level") == "CRITICAL"
            and dec.get("action") in ("HOLD", "MONITOR")
        ):
            return False, (
                f"Incohérence: risk=CRITICAL + action={dec.get('action')} "
                f"pour SKU {dec.get('sku', '?')}"
            )
    return True, ""


def _check_produit_pousser_when_urgent(state: RetailState) -> tuple[bool, str]:
    urgency = state.get("urgency_level", "LOW")
    if urgency in ("CRITICAL", "HIGH") and not state.get("produit_a_pousser"):
        return False, f"Urgence {urgency} sans produit_a_pousser défini"
    return True, ""


def _check_confidence_critical(state: RetailState) -> tuple[bool, str]:
    for dec in state.get("inventory_decisions", []):
        if dec.get("risk_level") == "CRITICAL":
            conf = dec.get("confidence")
            if isinstance(conf, float) and conf < 0.3:
                return False, (
                    f"Confidence {conf:.2f} < 0.3 sur SKU CRITICAL {dec.get('sku', '?')}"
                )
            if isinstance(conf, str) and conf == "low":
                return False, (
                    f"Confidence=low sur SKU CRITICAL {dec.get('sku', '?')} → escalade requise"
                )
    return True, ""


# ── Checks registry ────────────────────────────────────────────────────────────

_CHECKS = [
    ("conseil_min_length",           _check_conseil_length,                "REWRITE"),
    ("order_qty_positive",           _check_order_qty,                     "REWRITE"),
    ("escalation_flag",              _check_escalation_flag,               "ESCALATE"),
    ("critical_risk_action",         _check_critical_action_coherence,     "REWRITE"),
    ("produit_pousser_high_urgency", _check_produit_pousser_when_urgent,   "REWRITE"),
    ("confidence_critical",          _check_confidence_critical,           "ESCALATE"),
]


# ── Node LangGraph ─────────────────────────────────────────────────────────────

def guardrail_node(state: RetailState) -> Dict[str, Any]:
    """
    Node Critic/Guardrail — s'exécute après coach_fusion.

    Exécute tous les checks métier et détermine le verdict.
    Incrémente guardrail_cycles pour prévenir les boucles infinies.
    """
    cycles: int = int(state.get("guardrail_cycles", 0)) + 1
    checks: Dict[str, bool] = {}
    failures: List[str] = []    # (check_name, verdict_if_fail)
    escalate_reasons: List[str] = []

    for check_name, check_fn, fail_verdict in _CHECKS:
        passed, reason = check_fn(state)
        checks[check_name] = passed
        if not passed:
            failures.append((check_name, fail_verdict, reason))
            if fail_verdict == "ESCALATE":
                escalate_reasons.append(reason)

    # ── Verdict ───────────────────────────────────────────────────────────
    if not failures:
        verdict  = "APPROVE"
        feedback = None
    elif escalate_reasons:
        verdict  = "ESCALATE"
        feedback = " | ".join(escalate_reasons)
    elif any(fv == "BLOCK" for _, fv, _ in failures):
        verdict  = "BLOCK"
        feedback = " | ".join(r for _, _, r in failures)
    else:
        verdict  = "REWRITE"
        feedback = " | ".join(r for _, _, r in failures)

    # ── Log ───────────────────────────────────────────────────────────────
    if verdict == "APPROVE":
        logger.info(
            "[Guardrail] APPROVE — cycle=%s guardrail_cycles=%d",
            state.get("cycle_id"), cycles,
        )
    else:
        logger.warning(
            "[Guardrail] %s — cycle=%s guardrail_cycles=%d | %s",
            verdict, state.get("cycle_id"), cycles, feedback,
        )

    failed_checks = {n: False for n, _, _ in failures}
    passed_checks = {n: True  for n, _, _ in _CHECKS if n not in failed_checks}

    return {
        "guardrail_verdict":  verdict,
        "guardrail_feedback": feedback,
        "guardrail_checks":   {**passed_checks, **failed_checks},
        "guardrail_cycles":   cycles,
        "hitl_required":      (verdict == "ESCALATE") or state.get("hitl_required", False),
    }
