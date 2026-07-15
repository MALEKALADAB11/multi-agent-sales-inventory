"""
RetailState — Unified LangGraph State
======================================
Single shared TypedDict for the full agentic cycle:
  Sales branch (Analyst → Strategist)
  Inventory branch (Analysis → Decision)
  Knowledge branch (RAG)
  Context branch (Sentinel)
  CoachAgent (fusion)
  Guardrail Agent (validation)
  HITL (human-in-the-loop)

Source: ARCHITECTURE_AGENTIQUE_COMMUNICATION.md §4.2 RetailState partagé

Design rules:
  • All fields are optional (total=False) — agents write only their own fields
  • No field is removed when a branch fails — absent → treated as empty by next node
  • Observe the naming convention: <branch>_<field> for branch-specific outputs
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict


def _merge_dict(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Reducer LangGraph : fusion de dicts écrits par plusieurs branches parallèles."""
    return {**(a or {}), **(b or {})}


class RetailState(TypedDict, total=False):

    # ── Cycle identification ───────────────────────────────────────────────
    cycle_id:   str
    store_id:   str
    advisor_id: Optional[str]
    trigger_type: Literal[
        "scheduled_cycle",
        "advisor_message",
        "stock_event",
        "sales_event",
        "manager_action",
    ]

    # ── Raw inputs ─────────────────────────────────────────────────────────
    pos_data:     Dict[str, Any]   # from realtime POS feed
    stock_data:   Dict[str, Any]   # from inventory module
    context_data: Dict[str, Any]   # weather / events / promotions
    user_message: Optional[str]    # advisor chat message (if any)

    # ── Sales Branch outputs (AnalystAgent) ───────────────────────────────
    gap_pct:           float        # % gap vs daily target
    gap_objectif:      float        # alias historique consommé par main.py/_build_payload
    gap_amount:        float        # TND gap
    forecast_eod:      float        # end-of-day CA forecast
    coverage:          float        # % couverture gap par le forecast
    attainment:        float        # % atteinte objectif
    urgency_level:     str          # LOW / MEDIUM / HIGH / CRITICAL
    urgency_score:     float        # [0.0 – 1.0]
    analyst_summary:   str          # LLM-generated summary
    underperforming_categories: List[str]
    intraday_trend:    str          # "accelerating" | "stable" | "decelerating"

    # ── Strategy Branch outputs (StrategistAgent) ─────────────────────────
    strategie:         str          # strategic summary text
    strategie_actions: List[Dict[str, Any]]   # [{priorite, action, produit_cible, ...}]
    focus_produits:    List[str]    # product names to push
    cause_racine:      str          # root cause analysis
    message_manager:   str          # manager alert message
    real_time_alerts:  List[Dict[str, Any]]
    rag_used:          bool
    nb_rag_scripts:    int
    critique_score:    float        # Reflexion self-critique score [0–1]
    critique_passed:   bool

    # ── Inventory Branch outputs ──────────────────────────────────────────
    inventory_decisions:    List[Dict[str, Any]]   # per-SKU decisions
    critical_stock_alerts:  List[Dict[str, Any]]
    stock_report:           Dict[str, Any]
    inventory_snapshot:     Dict[str, Any]          # nb_ruptures, nb_critical, revenue_at_risk

    # ── Knowledge Branch outputs (RAG) ────────────────────────────────────
    rag_context:        Dict[str, Any]
    retrieved_scripts:  List[Dict[str, Any]]
    recommended_offers: List[Dict[str, Any]]

    # ── Context Branch outputs (Sentinel) ─────────────────────────────────
    context_report:    Dict[str, Any]
    external_context:  Dict[str, Any]           # weather, events, heatmap
    external_signals:  List[Dict[str, Any]]
    estimated_impact:  Dict[str, Any]
    context_heatmap:   Dict[str, Any]            # heatmap risques (stratège) → dashboard
    context_signals:   List[Dict[str, Any]]      # signaux contexte (stratège) → dashboard

    # ── CoachAgent outputs (cross-domain fusion) ──────────────────────────
    coach_recommendation:  Dict[str, Any]      # final ranked product recommendation
    scored_products:       List[Dict[str, Any]] # score_product() results
    advisor_message_final: str
    manager_message_final: str

    # ── Guardrail Agent outputs ───────────────────────────────────────────
    guardrail_status:          str             # APPROVE | REWRITE | ESCALATE | BLOCK
    guardrail_issues:          List[Dict[str, str]]
    guardrail_feedback:        str             # instruction for CoachAgent rewrite
    guardrail_confidence:      float
    requires_human_validation: bool
    guardrail_safe_fallback:   str

    # ── HITL ──────────────────────────────────────────────────────────────
    hitl_required: bool
    hitl_review_id: Optional[str]
    hitl_approved:  Optional[bool]
    hitl_approver:  Optional[str]

    # ── Observability ─────────────────────────────────────────────────────
    # Reducers obligatoires : ces trois canaux sont écrits par les 4 branches
    # parallèles dans le même superstep LangGraph — sans reducer, le graphe
    # lève InvalidUpdateError (« Can receive only one value per step »).
    # Contrat : chaque node ne retourne que SON delta (nouveaux éléments),
    # jamais la liste/dict cumulé lu depuis le state.
    agents_invoked:  Annotated[List[str], operator.add]
    total_latency_ms: int
    metrics:         Annotated[Dict[str, Any], _merge_dict]
    errors:          Annotated[List[str], operator.add]
    trace:           Any           # Langfuse trace handle (not serialized)
