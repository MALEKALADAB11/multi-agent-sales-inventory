"""
LangGraph Shared State — TypedDict partagé entre tous les agents.
Enrichi avec cycle_id, timestamps, métriques et logs pour le monitoring.
"""
from typing import Optional, Literal, List
from typing_extensions import TypedDict


class SalesAgentState(TypedDict, total=False):

    # ── Identifiants cycle ────────────────────────────────────────────────────
    cycle_id:       str           # UUID unique du cycle
    store_id:       str           # ID boutique (I63, M23, M03)
    triggered_by:   str           # cron | startup | event | manual
    started_at:     str           # ISO timestamp début cycle
    completed_at:   str           # ISO timestamp fin cycle

    # ── Données POS ───────────────────────────────────────────────────────────
    pos_data:       dict          # Flux POS temps réel
    pos_history:    List[dict]    # Historique transactions
    current_hour:   int           # Heure courante

    # ── Analyse Analyste ──────────────────────────────────────────────────────
    gap_objectif:   float         # Gap vs objectif journalier (%)
    gap_amount:     float         # Montant absolu du gap (TND)
    urgency_level:  Literal["HIGH", "MEDIUM", "LOW"]
    urgency_score:  float         # Score numérique 0-1
    forecast_eod:   float         # Prévision fin de journée
    forecast_mape:  float         # MAPE du modèle
    coverage:       float         # Couverture prévision vs gap
    attainment:     float         # % d'atteinte de l'objectif
    timesfm_prediction: Optional[dict]
    analyst_summary:    Optional[str]

    # ── Stratège ─────────────────────────────────────────────────────────────
    external_context:   Optional[dict]  # Météo + fériés + événements
    root_cause:         Optional[str]   # Cause racine analysée
    context_factors:    List[str]       # Facteurs contextuels
    strategie_data:     Optional[dict]  # Données brutes stratégie LLM
    strategie:          Optional[str]   # Résumé stratégie
    strategie_actions:  List[dict]      # Actions avec priorité, produit, argument
    focus_produits:     List[str]       # Produits à pousser
    message_manager:    Optional[str]   # Message pour le manager
    cause_racine:       Optional[str]   # Cause racine formatée
    context_heatmap:    Optional[dict]  # Heatmap risque horaire
    context_signals:    List[dict]      # Signaux contextuels (météo, stock, etc.)

    # ── RAG ───────────────────────────────────────────────────────────────────
    rag_context:        Optional[List[dict]]  # Scripts récupérés depuis Milvus
    rag_query:          Optional[str]         # Requête RAG utilisée
    rag_used:           bool                  # RAG utilisé dans ce cycle
    nb_rag_scripts:     int                   # Nombre de scripts récupérés

    # ── Coach ─────────────────────────────────────────────────────────────────
    conseil_final:      Optional[str]   # Conseil final généré
    feedback_history:   List[dict]      # Historique feedback conseillers

    # ── Métriques et monitoring ───────────────────────────────────────────────
    metrics: dict  # {
    #   cycle_id, started_at, completed_at, total_ms,
    #   nodes_executed, store_id, triggered_by,
    #   analyste_ms, stratege_ms, rag_ms, llm_ms,
    #   llm_calls, llm_tokens_est,
    # }

    # ── Logs agents (pour monitoring) ────────────────────────────────────────
    agent_logs: List[dict]  # [{agent, node, status, duration_ms, ...}]

    # ── Routing ───────────────────────────────────────────────────────────────
    route_to:   Optional[Literal["strategie", "coach", "end"]]
    errors:     List[str]   # Erreurs non bloquantes
    warnings:   List[str]   # Avertissements


# ── Alias ─────────────────────────────────────────────────────────────────────
AgentState = SalesAgentState


def initial_state(
    store_id:     str = "OOR_LAC_01",
    cycle_id:     str = "",
    triggered_by: str = "cron",
    **kwargs,
) -> SalesAgentState:
    """Crée un état initial complet pour un nouveau cycle."""
    from datetime import datetime
    import uuid

    if not cycle_id:
        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"

    now = datetime.utcnow().isoformat()

    return SalesAgentState(
        # ── Identifiants ──────────────────────────────
        cycle_id        = cycle_id,
        store_id        = store_id,
        triggered_by    = triggered_by,
        started_at      = now,
        completed_at    = "",
        current_hour    = datetime.now().hour,

        # ── POS ───────────────────────────────────────
        pos_data        = {"store_id": store_id, "cycle_id": cycle_id},
        pos_history     = [],

        # ── Analyse ───────────────────────────────────
        gap_objectif    = 0.0,
        gap_amount      = 0.0,
        urgency_level   = "LOW",
        urgency_score   = 0.0,
        forecast_eod    = 0.0,
        forecast_mape   = 0.0,
        coverage        = 100.0,
        attainment      = 0.0,
        timesfm_prediction = None,
        analyst_summary = None,

        # ── Stratège ──────────────────────────────────
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

        # ── RAG ───────────────────────────────────────
        rag_context     = None,
        rag_query       = None,
        rag_used        = False,
        nb_rag_scripts  = 0,

        # ── Coach ─────────────────────────────────────
        conseil_final   = None,
        feedback_history = [],

        # ── Métriques ─────────────────────────────────
        metrics = {
            "cycle_id":       cycle_id,
            "store_id":       store_id,
            "triggered_by":   triggered_by,
            "started_at":     now,
            "total_ms":       0,
            "nodes_executed": 0,
            "analyste_ms":    0,
            "stratege_ms":    0,
            "rag_ms":         0,
            "llm_ms":         0,
            "llm_calls":      0,
        },

        # ── Logs ──────────────────────────────────────
        agent_logs = [],

        # ── Routing ───────────────────────────────────
        route_to   = None,
        errors     = [],
        warnings   = [],
    )