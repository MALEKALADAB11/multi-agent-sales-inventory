"""
LangGraph Shared State — TypedDict partagé entre tous les agents.
"""
from typing import Optional, Literal
from typing_extensions import TypedDict


class UrgencyLevel(str):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SalesAgentState(TypedDict, total=False):
    # --- Données POS ---
    pos_data: dict                        # Flux POS temps réel
    pos_history: list[dict]               # Historique BigQuery

    # --- Analyse ---
    gap_objectif: float                   # Gap vs objectif journalier (%)
    gap_amount: float                     # Montant absolu du gap
    urgency_level: Literal["HIGH", "MEDIUM", "LOW"]
    urgency_score: float                  # Score numérique 0-1
    timesfm_prediction: Optional[dict]    # Prévision TimesFM fin de journée
    analyst_summary: Optional[str]        # Résumé textuel de l'analyse

    # --- Stratégie ---
    strategie: Optional[str]
    rag_context: Optional[list[dict]]
     # ── Stratège ─────────────────────────────────────────
    external_context:    Optional[dict]   # ← NOUVEAU
    root_cause:          Optional[str]    # ← NOUVEAU
    context_factors:     list[str]        # ← NOUVEAU
    strategie_data:      Optional[dict]   # ← NOUVEAU
    strategie:           Optional[str]
    strategie_actions:   list[dict]       # ← NOUVEAU
    focus_produits:      list[str]        # ← NOUVEAU
    message_manager:     Optional[str]    # ← NOUVEAU
    cause_racine:        Optional[str]    # ← NOUVEAU
    context_heatmap:     Optional[dict]   # ← NOUVEAU
    context_signals:     list[dict]       # ← NOUVEAU


    # --- Conseil final ---
    conseil_final: Optional[str]

    # --- Feedback Memory ---
    feedback_history: list[dict]

    # --- Routing flags ---
    route_to:Optional[Literal["strategie", "coach", "end"]]
    errors:  list[str]
    metrics: dict
AgentState = SalesAgentState
# ── Alias pour compatibilité avec main.py ─────────────────────
def initial_state(store_id: str = "OOR_LAC_01", **kwargs) -> SalesAgentState:
    """Accepte et ignore les kwargs supplémentaires comme cycle_id."""
    return SalesAgentState(
        pos_data           = {"store_id": store_id},
        pos_history        = [],
        gap_objectif       = 0.0,
        gap_amount         = 0.0,
        urgency_level      = "LOW",
        urgency_score      = 0.0,
        timesfm_prediction = None,
        analyst_summary    = None,
        external_context   = None,
        root_cause         = None,
        context_factors    = [],
        strategie_data     = None,
        strategie          = None,
        strategie_actions  = [],
        focus_produits     = [],
        message_manager    = None,
        cause_racine       = None,
        context_heatmap    = None,
        context_signals    = [],
        conseil_final      = None,
        feedback_history   = [],
        route_to           = None,
        errors             = [],
        metrics            = {}
    )