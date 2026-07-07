"""
CoachAgent Cross-Domain — fusion Sales × Inventory × Knowledge.

Pattern : Hybrid (LLM synthesis + rule-based fallback).

Reçoit les outputs des 3 branches parallèles via RetailState.
Produit une recommandation unifiée pour le conseiller de vente :
  - conseil_personnalise  : conseil actionable 2-3 phrases
  - produit_a_pousser     : SKU/produit prioritaire à vendre
  - produit_a_eviter      : SKU/produit à éviter (stock critique)
  - justification_metier  : explication cohérence Sales × Inventory

Inputs depuis RetailState :
  stock_report, context_report, inventory_decisions, alerts  [branche inventory]
  sales_report, strategy_actions, analyst_summary, focus_produits [branche sales]
  rag_scripts, market_signals  [branche knowledge]

Outputs vers RetailState :
  conseil_personnalise, produit_a_pousser, produit_a_eviter,
  justification_metier, recommendation
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from .state import RetailState


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_fusion_prompt(state: RetailState) -> str:
    stock_report     = state.get("stock_report", {})
    context_report   = state.get("context_report", {})
    sales_report     = state.get("sales_report", {})
    strategy_actions = state.get("strategy_actions", [])
    rag_scripts      = state.get("rag_scripts", [])
    urgency          = state.get("urgency_level", "LOW")
    cause_racine     = state.get("cause_racine") or ""
    analyst_summary  = state.get("analyst_summary") or ""
    focus_produits   = state.get("focus_produits", [])
    market_signals   = state.get("market_signals", {})

    # Inventory context
    risk_level    = stock_report.get("risk_assessment", {}).get("level", "N/A")
    days_of_stock = stock_report.get("metrics", {}).get("days_of_stock_remaining", "N/A")

    inv_decisions = state.get("inventory_decisions", [])
    inv_action    = inv_decisions[0].get("action", "HOLD")  if inv_decisions else "HOLD"
    inv_sku       = inv_decisions[0].get("sku", "")         if inv_decisions else ""
    inv_qty       = inv_decisions[0].get("order_qty", "")   if inv_decisions else ""

    uplift_pct      = context_report.get("demand_uplift_pct", 0)
    dominant_signal = context_report.get("dominant_signal", "")

    # Format strategy actions (top 5)
    strategy_txt = "\n".join(
        f"  • {a.get('action', a.get('description', str(a)))}"
        for a in strategy_actions[:5]
    ) if strategy_actions else "  Aucune action stratégique."

    # Format RAG scripts (top 3, tronqués)
    rag_txt = "\n".join(
        f"  [{i+1}] {s.get('title', 'Script')}: {str(s.get('content', ''))[:200]}"
        for i, s in enumerate(rag_scripts[:3])
    ) if rag_scripts else "  Aucun script RAG pertinent."

    # Market signals summary
    signals_txt = ""
    if market_signals:
        signals_txt = "Signaux marché: " + ", ".join(
            f"{k}={v}" for k, v in list(market_signals.items())[:5]
        )

    focus_txt = ", ".join(focus_produits[:5]) if focus_produits else "N/A"

    return f"""Tu es le CoachAgent retail Ooredoo Tunisia — rôle : fusionner les analyses Sales et Inventory pour conseiller un vendeur en magasin.

═══ CONTEXTE INVENTAIRE ═══
SKU prioritaire : {inv_sku or 'N/A'}
Niveau de risque stock : {risk_level}
Jours de stock restants : {days_of_stock}
Décision recommandée : {inv_action}{f' (qty={inv_qty})' if inv_qty else ''}
Ajustement demande : {uplift_pct:+.1f}% (signal : {dominant_signal or 'aucun'})

═══ CONTEXTE VENTES ═══
Urgence : {urgency}
Résumé analyste : {analyst_summary[:200] if analyst_summary else 'N/A'}
Cause racine du gap : {cause_racine[:150] if cause_racine else 'N/A'}
Produits focus conseiller : {focus_txt}
{signals_txt}

═══ ACTIONS STRATÉGIQUES ═══
{strategy_txt}

═══ SCRIPTS RAG PERTINENTS ═══
{rag_txt}

═══ MISSION ═══
Produis une recommandation cohérente Sales × Inventory en JSON strict (pas de markdown autour) :

{{
  "conseil_personnalise": "<conseil actionable 2-3 phrases, spécifique au contexte, ton coach direct>",
  "produit_a_pousser": "<SKU ou nom produit à vendre en priorité — stock disponible + levier vente>",
  "produit_a_eviter": "<SKU ou nom produit à ne pas promettre au client — stock critique>",
  "justification_metier": "<1 phrase expliquant pourquoi ce conseil est cohérent Sales×Inventory>"
}}"""


# ── Rule-based fallback ────────────────────────────────────────────────────────

def _rule_based_coach(state: RetailState) -> Dict[str, Any]:
    """Fallback déterministe — pas de LLM requis."""
    inv_decisions  = state.get("inventory_decisions", [])
    focus          = state.get("focus_produits", [])
    urgency        = state.get("urgency_level", "LOW")
    analyst_sum    = state.get("analyst_summary") or ""
    strategy_acts  = state.get("strategy_actions", [])
    cause          = state.get("cause_racine") or ""

    # Produit à éviter = premier SKU avec ORDER ou EXPEDITE
    produit_a_eviter = ""
    for dec in inv_decisions:
        if dec.get("action") in ("ORDER", "EXPEDITE"):
            produit_a_eviter = dec.get("sku", "")
            break

    # Produit à pousser = premier focus_produits disponible ≠ produit_a_eviter
    produit_a_pousser = ""
    for p in focus:
        if p != produit_a_eviter:
            produit_a_pousser = p
            break

    # Conseil à partir des actions stratégiques + summary
    if strategy_acts:
        first_action = strategy_acts[0].get("action", strategy_acts[0].get("description", ""))
        conseil_base = f"{first_action}."
    elif analyst_sum:
        conseil_base = analyst_sum[:180].rstrip(".")  + "."
    else:
        conseil_base = f"Urgence {urgency}: concentrez vos efforts sur les produits disponibles."

    conseil = conseil_base
    if produit_a_pousser:
        conseil += f" Priorité : {produit_a_pousser}."
    if cause:
        conseil += f" Cause identifiée : {cause[:80]}."

    justif = (
        f"Règle: pousser {produit_a_pousser or 'produits focus'} "
        f"(stock OK), éviter {produit_a_eviter or 'SKUs ORDER/EXPEDITE'} (stock critique)."
    )

    return {
        "conseil_personnalise": conseil,
        "produit_a_pousser":    produit_a_pousser,
        "produit_a_eviter":     produit_a_eviter,
        "justification_metier": justif,
        "recommendation": {
            "source":  "rule_based",
            "conseil": conseil,
            "urgency": state.get("urgency_level", "LOW"),
        },
    }


# ── LLM fusion ────────────────────────────────────────────────────────────────

def _llm_coach(state: RetailState, llm) -> Dict[str, Any]:
    """Fusion LLM — lève une exception si le parsing échoue."""
    from langchain_core.messages import HumanMessage

    prompt   = _build_fusion_prompt(state)
    response = llm.invoke([HumanMessage(content=prompt)])
    text     = response.content.strip()

    # Extraire le JSON — supporte ```json ... ``` ou texte brut
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    parsed = json.loads(text)

    conseil = parsed.get("conseil_personnalise", "")
    return {
        "conseil_personnalise": conseil,
        "produit_a_pousser":    parsed.get("produit_a_pousser", ""),
        "produit_a_eviter":     parsed.get("produit_a_eviter", ""),
        "justification_metier": parsed.get("justification_metier", ""),
        "recommendation": {
            "source":  "llm",
            "conseil": conseil,
            "urgency": state.get("urgency_level", "LOW"),
        },
    }


# ── Node LangGraph ─────────────────────────────────────────────────────────────

def make_coach_fusion_node(llm=None) -> Callable:
    """
    Factory : retourne un node LangGraph pour le CoachAgent.

    Args:
        llm: LangChain LLM instance (optionnel — si None, fallback rule-based)

    Returns:
        Callable compatible LangGraph node (state → dict updates)
    """
    def coach_fusion_node(state: RetailState) -> Dict[str, Any]:
        cycle = state.get("cycle_id", "?")

        if llm is None:
            result = _rule_based_coach(state)
            logger.info("[Coach] Rule-based — cycle=%s", cycle)
            return result

        try:
            result = _llm_coach(state, llm)
            logger.info(
                "[Coach] LLM fusion OK — cycle=%s | produit_pousser=%s",
                cycle, result.get("produit_a_pousser"),
            )
            return result
        except Exception as exc:
            logger.warning("[Coach] LLM failed (%s) — fallback rule-based", exc)
            result = _rule_based_coach(state)
            result["recommendation"]["source"] = "rule_based_fallback"
            return result

    return coach_fusion_node
