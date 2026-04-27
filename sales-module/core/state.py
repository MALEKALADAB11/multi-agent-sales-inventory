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

    # --- Conseil final ---
    conseil_final: Optional[str]

    # --- Feedback Memory ---
    feedback_history: list[dict]

    # --- Routing flags ---
    route_to: Optional[Literal["strategie", "coach", "end"]]
    error: Optional[str]