# 

"""
LangGraph Shared State — TypedDict partagé entre tous les agents.
"""

from typing import Optional, Literal, List
from typing_extensions import TypedDict


class SalesAgentState(TypedDict, total=False):
    cycle_id: str
    store_id: str
    triggered_by: str
    started_at: str
    completed_at: str

    pos_data: dict
    pos_history: List[dict]
    current_hour: int

    data_quality: Optional[dict]
    analysis_features: Optional[dict]
    analyst_output: Optional[dict]

    gap_objectif: float
    gap_amount: float
    urgency_level: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    urgency_score: float
    forecast_eod: float
    forecast_mape: float
    coverage: float
    attainment: float
    timesfm_prediction: Optional[dict]
    analyst_summary: Optional[str]

    external_context: Optional[dict]
    root_cause: Optional[str]
    context_factors: List[str]
    strategie_data: Optional[dict]
    strategie: Optional[str]
    strategie_actions: List[dict]
    focus_produits: List[str]
    message_manager: Optional[str]
    cause_racine: Optional[str]
    context_heatmap: Optional[dict]
    context_signals: List[dict]

    rag_context: Optional[List[dict]]
    rag_query: Optional[str]
    rag_used: bool
    nb_rag_scripts: int

    conseil_final: Optional[str]
    feedback_history: List[dict]

    metrics: dict
    agent_logs: List[dict]

    route_to: Optional[Literal["strategie", "coach", "end"]]
    errors: List[str]
    warnings: List[str]

    # ── Champs v5 (ajoutés en Phase 1) ───────────────────────────────────────
    # Phase 2 — Inventory Cross-Reference
    inventory_snapshot: Optional[dict]         # snapshot stock depuis Redis
    stock_filtered_products: List[str]         # SKUs avec stock > 0 dispo

    # Phase 3 — Critique Agent
    critique_score: float                      # 0.0 → 1.0
    critique_passed: bool
    critique_checks: Optional[dict]            # {check_name: {passed, score, reason}}
    critique_feedback: Optional[str]           # feedback textuel au Stratège
    critique_cycles: int                       # nb révisions itératives

    # Phase 4 — Human-in-the-Loop
    hitl_required: bool
    hitl_approved: bool
    hitl_approver: Optional[str]

    # Phase 5 — Procedural Memory & Feedback
    procedural_rules: List[dict]               # règles apprises [{rule, confidence, source}]
    feedback_score: Optional[float]            # score retour CA réel

    # Phase 4 — Supervisor routing
    supervisor_routing: Optional[dict]
    circuit_states: Optional[dict]            # snapshot états CB

    # Phase 4 — Extension v5 sérialisée (UnifiedCycleState)
    v5_extension: Optional[dict]

    # Coach-Stratège Orchestrator (v2)
    strategie_source: Optional[str]           # "success"|"cached"|"stale_cache"|"fallback"|"pre_loaded"
    strategie_cached: Optional[bool]          # True si retour depuis cache LRU


AgentState = SalesAgentState


def initial_state(
    store_id: str = "I63",
    cycle_id: str = "",
    triggered_by: str = "cron",
    **kwargs,
) -> SalesAgentState:
    from datetime import datetime
    import uuid

    if not cycle_id:
        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"

    now = datetime.utcnow().isoformat()

    return SalesAgentState(
        cycle_id=cycle_id,
        store_id=store_id,
        triggered_by=triggered_by,
        started_at=now,
        completed_at="",
        current_hour=datetime.now().hour,

        pos_data={"store_id": store_id, "cycle_id": cycle_id},
        pos_history=[],

        data_quality=None,
        analysis_features=None,
        analyst_output=None,

        gap_objectif=0.0,
        gap_amount=0.0,
        urgency_level="LOW",
        urgency_score=0.0,
        forecast_eod=0.0,
        forecast_mape=0.0,
        coverage=100.0,
        attainment=0.0,
        timesfm_prediction=None,
        analyst_summary=None,

        external_context=None,
        root_cause=None,
        context_factors=[],
        strategie_data=None,
        strategie=None,
        strategie_actions=[],
        focus_produits=[],
        message_manager=None,
        cause_racine=None,
        context_heatmap=None,
        context_signals=[],

        rag_context=None,
        rag_query=None,
        rag_used=False,
        nb_rag_scripts=0,

        conseil_final=None,
        feedback_history=[],

        metrics={
            "cycle_id": cycle_id,
            "store_id": store_id,
            "triggered_by": triggered_by,
            "started_at": now,
            "total_ms": 0,
            "nodes_executed": 0,
            "analyste_ms": 0,
            "stratege_ms": 0,
            "rag_ms": 0,
            "llm_ms": 0,
            "llm_calls": 0,
        },

        agent_logs=[],

        route_to=None,
        errors=[],
        warnings=[],

        # ── Champs v5 ────────────────────────────────────────────────────────
        inventory_snapshot=None,
        stock_filtered_products=[],

        critique_score=0.0,
        critique_passed=False,
        critique_checks=None,
        critique_feedback=None,
        critique_cycles=0,

        hitl_required=False,
        hitl_approved=False,
        hitl_approver=None,

        procedural_rules=[],
        feedback_score=None,

        supervisor_routing=None,
        circuit_states=None,
        v5_extension=None,
    )
