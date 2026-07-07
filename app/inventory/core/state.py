"""
UnifiedRetailState — état partagé cross-domaine entre tous les agents.

Conçu pour le SupervisorAgent LangGraph qui orchestre 3 branches parallèles
(Sales, Inventory, Knowledge) via Send() puis les fusionne dans CoachAgent.

Reducers Annotated :
  - operator.add  → liste accumulée (toutes branches contribuent)
  - _merge_dicts  → dict fusion shallow, dernier écrit gagne par clé
  - _last_wins    → scalaire, dernier écrit gagne

Seuls les champs annotés sont fusionnés par LangGraph lors du fan-out.
Les scalaires non-annotés sont simplement écrasés (last writer wins).
"""
from __future__ import annotations

import operator
import uuid
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict
from app.core.config import DEFAULT_STORE_ID


# ── Reducers ──────────────────────────────────────────────────────────────────

def _merge_dicts(a: Dict, b: Dict) -> Dict:
    """Fusion shallow: b écrase les clés de a (idempotent sur dict vide)."""
    if not a:
        return b or {}
    if not b:
        return a
    return {**a, **b}


def _last_wins(a: Any, b: Any) -> Any:
    """Scalaire: le dernier écrivain gagne."""
    return b if b is not None else a


# ── État unifié ───────────────────────────────────────────────────────────────

class RetailState(TypedDict, total=False):
    """
    État unifié du cycle retail — flux à travers le SupervisorAgent.

    Champs par section :
      [identité]   → cycle_id, store_id, sku, business_objective
      [inventory]  → stock_report, context_report, inventory_decisions, alerts
      [sales]      → sales_report, strategy_actions, analyst_summary, …
      [knowledge]  → rag_scripts, market_signals, context_signals
      [coach]      → conseil_personnalise, produit_a_pousser, …
      [guardrail]  → guardrail_verdict, guardrail_feedback, guardrail_cycles
      [hitl]       → hitl_required, hitl_approved, …
      [meta]       → branch_status, errors, warnings, timing
    """

    # ── Identité du cycle ──────────────────────────────────────────────────
    cycle_id:           str
    store_id:           str
    sku:                str
    business_objective: str
    triggered_by:       str
    started_at:         str
    completed_at:       Optional[str]

    # ── Branche Inventory ──────────────────────────────────────────────────
    # InventoryAnalysisAgent + InventoryDecisionAgent outputs
    stock_report:        Annotated[Dict[str, Any], _merge_dicts]
    context_report:      Annotated[Dict[str, Any], _merge_dicts]
    inventory_decisions: Annotated[List[Dict[str, Any]], operator.add]
    alerts:              Annotated[List[Dict[str, Any]], operator.add]

    # ── Branche Sales ──────────────────────────────────────────────────────
    # AnalystAgent (ReAct) + StrategistAgent (Reflexion) outputs
    sales_report:     Annotated[Dict[str, Any], _merge_dicts]
    strategy_actions: Annotated[List[Dict[str, Any]], operator.add]
    analyst_summary:  Optional[str]
    cause_racine:     Optional[str]
    focus_produits:   Annotated[List[str], operator.add]
    urgency_level:    Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    urgency_score:    float

    # ── Branche Knowledge / RAG + Context Sentinel ────────────────────────
    rag_scripts:     Annotated[List[Dict[str, Any]], operator.add]
    market_signals:  Annotated[Dict[str, Any], _merge_dicts]
    context_signals: Annotated[List[Dict[str, Any]], operator.add]

    # ── CoachAgent (Cross-Domain Fusion) ───────────────────────────────────
    conseil_personnalise: Optional[str]
    produit_a_pousser:    Optional[str]
    produit_a_eviter:     Optional[str]
    justification_metier: Optional[str]
    recommendation:       Annotated[Dict[str, Any], _merge_dicts]

    # ── Guardrail / Critique ───────────────────────────────────────────────
    guardrail_verdict:  Optional[Literal["APPROVE", "REWRITE", "ESCALATE", "BLOCK"]]
    guardrail_feedback: Optional[str]
    guardrail_checks:   Annotated[Dict[str, bool], _merge_dicts]
    guardrail_cycles:   int

    # ── Human-in-the-Loop ─────────────────────────────────────────────────
    hitl_required: bool
    hitl_approved: bool
    hitl_approver: Optional[str]
    hitl_comment:  Optional[str]

    # ── Métadonnées pipeline ───────────────────────────────────────────────
    branch_status: Annotated[Dict[str, str], _merge_dicts]
    errors:        Annotated[List[str], operator.add]
    warnings:      Annotated[List[str], operator.add]
    total_ms:      Optional[int]


# ── Factory ───────────────────────────────────────────────────────────────────

def initial_retail_state(
    store_id:           str = DEFAULT_STORE_ID,
    sku:                str = "",
    business_objective: str = "balanced",
    cycle_id:           str = "",
    triggered_by:       str = "api",
) -> RetailState:
    """Crée un RetailState initialisé avec des valeurs par défaut sûres."""
    if not cycle_id:
        cycle_id = f"retail-{uuid.uuid4().hex[:8]}"

    return RetailState(
        cycle_id=cycle_id,
        store_id=store_id,
        sku=sku,
        business_objective=business_objective,
        triggered_by=triggered_by,
        started_at=datetime.utcnow().isoformat(),
        completed_at=None,

        stock_report={},
        context_report={},
        inventory_decisions=[],
        alerts=[],

        sales_report={},
        strategy_actions=[],
        analyst_summary=None,
        cause_racine=None,
        focus_produits=[],
        urgency_level="LOW",
        urgency_score=0.0,

        rag_scripts=[],
        market_signals={},
        context_signals=[],

        conseil_personnalise=None,
        produit_a_pousser=None,
        produit_a_eviter=None,
        justification_metier=None,
        recommendation={},

        guardrail_verdict=None,
        guardrail_feedback=None,
        guardrail_checks={},
        guardrail_cycles=0,

        hitl_required=False,
        hitl_approved=False,
        hitl_approver=None,
        hitl_comment=None,

        branch_status={
            "sales":     "pending",
            "inventory": "pending",
            "knowledge": "pending",
        },
        errors=[],
        warnings=[],
        total_ms=None,
    )
