"""
SupervisorAgent — Orchestrateur LangGraph multi-branches parallèles.

Architecture (diagram ARCHITECTURE_AGENTS.md) :

    supervisor  ──Send()──►  sales_branch      (AnalystAgent + StrategistAgent)
                ──Send()──►  inventory_branch  (AnalysisAgent + ContextAgent + DecisionAgent)
                ──Send()──►  knowledge_branch  (KnowledgeRAG + ContextSentinel)
                                ↓ (convergence LangGraph — reducers RetailState)
                         coach_fusion          (CoachAgent cross-domain)
                                ↓
                           guardrail           (Critic / Guardrail Agent)
                                ↓
                  ┌────────────────────────────┐
                  │ APPROVE → output           │
                  │ REWRITE → coach_fusion     │  (max 2 cycles)
                  │ ESCALATE → human_review    │
                  │ BLOCK   → END              │
                  └────────────────────────────┘

Performances :
  - Send() = fan-out natif LangGraph → branches exécutées en parallèle
  - Compiled graph = singleton (compilé une seule fois par process)
  - Reducers annotés dans RetailState → fusion zero-copy des branches
  - Chaque branche est isolée : échec ≠ arrêt des autres
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from .state import RetailState

logger = logging.getLogger(__name__)

# Factory import — résolu lazily pour éviter circular imports
def _get_coach_node():
    from app.inventory.core.coach_agent import make_coach_fusion_node
    from app.inventory.utils.llm_factory import get_smart_llm
    try:
        llm = get_smart_llm()
    except Exception:
        llm = None
    return make_coach_fusion_node(llm=llm)


def _get_guardrail_node():
    from app.inventory.core.guardrail import guardrail_node
    return guardrail_node

_MAX_GUARDRAIL_CYCLES = 2

# Singleton global — compilé une fois, partagé entre toutes les requêtes
_supervisor_graph: Any = None


# ── Nodes internes ─────────────────────────────────────────────────────────────

def _supervisor_entry(state: RetailState) -> Dict[str, Any]:
    """Entry node — initialise cycle_id et branch_status si absents."""
    updates: Dict[str, Any] = {}
    if not state.get("cycle_id"):
        updates["cycle_id"] = f"retail-{uuid.uuid4().hex[:8]}"
    if not state.get("started_at"):
        updates["started_at"] = datetime.utcnow().isoformat()
    updates["branch_status"] = {
        "sales":     "pending",
        "inventory": "pending",
        "knowledge": "pending",
    }
    logger.info(
        "[Supervisor] Cycle démarré — store=%s sku=%s cycle=%s",
        state.get("store_id"), state.get("sku"),
        updates.get("cycle_id") or state.get("cycle_id"),
    )
    return updates


def _output_node(state: RetailState) -> Dict[str, Any]:
    """Output node — calcule total_ms et marque completed_at."""
    started  = state.get("started_at", "")
    total_ms: Optional[int] = None
    if started:
        try:
            delta    = datetime.utcnow() - datetime.fromisoformat(started)
            total_ms = int(delta.total_seconds() * 1000)
        except Exception:
            pass
    logger.info(
        "[Supervisor] Cycle terminé — cycle=%s total_ms=%s verdict=%s",
        state.get("cycle_id"), total_ms, state.get("guardrail_verdict"),
    )
    return {
        "completed_at": datetime.utcnow().isoformat(),
        "total_ms":     total_ms,
    }


# ── Routing functions ──────────────────────────────────────────────────────────

def _route_parallel_branches(state: RetailState) -> List[Send]:
    """
    Fan-out parallèle : lance les 3 branches simultanément via Send().
    LangGraph exécute chaque Send indépendamment et fusionne les états
    via les reducers annotés de RetailState.
    """
    base = dict(state)
    return [
        Send("inventory_branch", base),
        Send("knowledge_branch", base),
        Send("sales_branch",     base),
    ]


def _route_after_guardrail(state: RetailState) -> str:
    """
    Routing après le Guardrail :
      APPROVE  → output
      REWRITE  → coach_fusion  (max _MAX_GUARDRAIL_CYCLES)
      ESCALATE → human_review
      BLOCK    → END
    """
    verdict = state.get("guardrail_verdict", "APPROVE")
    cycles  = int(state.get("guardrail_cycles", 0))

    if verdict == "APPROVE":
        return "output"
    if verdict == "BLOCK":
        logger.warning("[Supervisor] BLOCK — cycle=%s", state.get("cycle_id"))
        return END
    if verdict == "ESCALATE" or state.get("hitl_required"):
        return "human_review"
    if verdict == "REWRITE" and cycles < _MAX_GUARDRAIL_CYCLES:
        logger.info(
            "[Supervisor] REWRITE cycle %d/%d — cycle=%s",
            cycles, _MAX_GUARDRAIL_CYCLES, state.get("cycle_id"),
        )
        return "coach_fusion"
    # Cycles max atteints : force approve
    logger.warning(
        "[Supervisor] Max guardrail cycles (%d) — force APPROVE — cycle=%s",
        _MAX_GUARDRAIL_CYCLES, state.get("cycle_id"),
    )
    return "output"


# ── Builder ────────────────────────────────────────────────────────────────────

def build_supervisor_graph(
    inventory_branch_fn: Callable,
    sales_branch_fn:     Callable,
    knowledge_branch_fn: Callable,
    coach_fusion_fn:     Callable,
    guardrail_fn:        Callable,
    human_review_fn:     Optional[Callable] = None,
) -> Any:
    """
    Construit et compile le graphe LangGraph du SupervisorAgent.

    Tous les arguments sont des callables (nodes LangGraph) :
      inventory_branch_fn  — exécute Analysis + Context + Decision en parallèle interne
      sales_branch_fn      — exécute Analyst ReAct + Stratège Reflexion
      knowledge_branch_fn  — exécute KnowledgeRAG + ContextSentinel
      coach_fusion_fn      — fusionne les 3 branches (CoachAgent)
      guardrail_fn         — valide la recommandation (Critic Agent)
      human_review_fn      — optionnel : HITL handler

    Returns:
        CompiledStateGraph — prêt pour .invoke() ou .astream()
    """
    g = StateGraph(RetailState)

    # ── Nodes ──────────────────────────────────────────────────────────────────
    g.add_node("supervisor",       _supervisor_entry)
    g.add_node("inventory_branch", inventory_branch_fn)
    g.add_node("sales_branch",     sales_branch_fn)
    g.add_node("knowledge_branch", knowledge_branch_fn)
    g.add_node("coach_fusion",     coach_fusion_fn)
    g.add_node("guardrail",        guardrail_fn)
    g.add_node("output",           _output_node)

    has_hitl = human_review_fn is not None
    if has_hitl:
        g.add_node("human_review", human_review_fn)

    # ── Entry ──────────────────────────────────────────────────────────────────
    g.set_entry_point("supervisor")

    # ── Fan-out parallèle via Send() ──────────────────────────────────────────
    g.add_conditional_edges(
        "supervisor",
        _route_parallel_branches,
        ["sales_branch", "inventory_branch", "knowledge_branch"],
    )

    # ── Convergence → CoachAgent ──────────────────────────────────────────────
    for branch in ("sales_branch", "inventory_branch", "knowledge_branch"):
        g.add_edge(branch, "coach_fusion")

    # ── Coach → Guardrail ─────────────────────────────────────────────────────
    g.add_edge("coach_fusion", "guardrail")

    # ── Guardrail → routing conditionnel ─────────────────────────────────────
    routing_map: Dict[str, str] = {
        "output":       "output",
        END:            END,
        "coach_fusion": "coach_fusion",
    }
    if has_hitl:
        routing_map["human_review"] = "human_review"
        g.add_edge("human_review", "output")

    g.add_conditional_edges("guardrail", _route_after_guardrail, routing_map)
    g.add_edge("output", END)

    compiled = g.compile()
    logger.info(
        "[Supervisor] Graphe compilé — %d nodes | fan-out 3 branches | guardrail max=%d cycles",
        7 + (1 if has_hitl else 0),
        _MAX_GUARDRAIL_CYCLES,
    )
    return compiled


def get_or_build_supervisor(
    inventory_branch_fn: Callable,
    sales_branch_fn:     Callable,
    knowledge_branch_fn: Callable,
    coach_fusion_fn:     Callable,
    guardrail_fn:        Callable,
    human_review_fn:     Optional[Callable] = None,
) -> Any:
    """
    Singleton : compile le graphe une seule fois par process.

    Idempotent — appels successifs retournent le même objet compilé.
    Le graphe LangGraph compilé est stateless entre les invoke() :
    l'état complet passe en paramètre, rien n'est stocké sur le graph.
    """
    global _supervisor_graph
    if _supervisor_graph is None:
        _supervisor_graph = build_supervisor_graph(
            inventory_branch_fn=inventory_branch_fn,
            sales_branch_fn=sales_branch_fn,
            knowledge_branch_fn=knowledge_branch_fn,
            coach_fusion_fn=coach_fusion_fn,
            guardrail_fn=guardrail_fn,
            human_review_fn=human_review_fn,
        )
    return _supervisor_graph
