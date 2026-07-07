"""Documentation en base : COMMENT ON sur schémas et tables applicatives.

Le schéma public héberge la plomberie applicative (auth, monitoring agents,
KPI, RAG, HITL) : on documente l'appartenance de chaque table plutôt que de
les déplacer (tout le SQL applicatif non qualifié résout vers public via
search_path — un déplacement physique casserait sans rien apporter).

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_COMMENTS = {
    # Schémas
    "SCHEMA sales": "Domaine ventes : référentiels boutiques/produits/agents, transactions batch + temps réel, objectifs, scripts coaching RAG.",
    "SCHEMA inventory": "Domaine stocks : niveaux, historiques ventes/stocks, prévisions, alertes et recommandations des agents IA.",
    "SCHEMA supply": "Domaine approvisionnement : fournisseurs, catalogue de sourcing, bons de commande, mouvements, transferts.",
    "SCHEMA market": "Intelligence marché : concurrents, prix, événements, flux MNP, saisonnalité.",
    "SCHEMA customer": "Client : segments, NPS/CSAT.",
    "SCHEMA coaching": "Coaching commercial : événements de coaching générés par les agents.",
    # Tables public (appartenance)
    "TABLE public.app_users": "[AUTH] Comptes applicatifs (managers, vendeurs).",
    "TABLE public.app_sessions": "[AUTH] Sessions par token.",
    "TABLE public.agent_logs": "[MONITORING] Logs par node LangGraph.",
    "TABLE public.agent_cycles": "[MONITORING] Cycles d'orchestration complets.",
    "TABLE public.agent_errors": "[MONITORING] Erreurs agents, résolubles depuis le panel.",
    "TABLE public.agent_memory": "[MONITORING] Mémoire épisodique de l'agent analyste.",
    "TABLE public.agent_sessions": "[MONITORING] Sessions de conversation agents.",
    "TABLE public.rag_feedback": "[RAG] Feedback d'utilité des scripts récupérés.",
    "TABLE public.rag_queries": "[RAG] Requêtes de récupération loggées.",
    "TABLE public.rag_feedback_metrics": "[RAG] Agrégats de qualité RAG.",
    "TABLE public.coach_interactions": "[COACHING] Historique conversations coach↔vendeur.",
    "TABLE public.hitl_reviews": "[HITL] Décisions en attente de validation humaine.",
    "TABLE public.agent_kpi_daily": "[KPI] KPI journaliers par vendeur.",
    "TABLE public.store_kpi_daily": "[KPI] KPI journaliers par boutique.",
    "TABLE public.telco_targets_monthly": "[KPI] Objectifs mensuels télécom.",
    "TABLE public.weekly_kpi_summary": "[KPI] Synthèse hebdomadaire.",
}


def upgrade() -> None:
    for target, comment in _COMMENTS.items():
        escaped = comment.replace("'", "''")
        op.execute(f"COMMENT ON {target} IS '{escaped}'")


def downgrade() -> None:
    for target in _COMMENTS:
        op.execute(f"COMMENT ON {target} IS NULL")
