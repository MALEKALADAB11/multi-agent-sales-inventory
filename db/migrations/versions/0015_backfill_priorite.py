"""Backfill urgence/priorité des recommandations et bons de commande.

Bug corrigé côté code (2026-07-28) : le DecisionAgent ne persistait pas
`urgency` sur inventory.recommendations, et create_suggestion_from_recommendation
n'écrivait pas `priorite` sur supply.purchase_orders — d'où une priorité toujours
« NORMAL » sur le Kanban Achats.

Les lignes déjà en base ont urgency=NULL / priorite=NORMAL. On les récupère depuis
le texte déterministe de la recommandation (« aujourd'hui » = immediate/URGENTE,
« cette semaine » = this_week/HAUTE, « ce mois-ci » = this_month/NORMAL). Seules
les lignes antérieures au fix (po.urgency IS NULL) sont touchées.

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Récupère l'urgence des recommandations depuis leur texte déterministe.
    op.execute("""
        UPDATE inventory.recommendations
        SET urgency = CASE
            WHEN recommendation_text ILIKE '%aujourd''hui%'  THEN 'immediate'
            WHEN recommendation_text ILIKE '%cette semaine%' THEN 'this_week'
            WHEN recommendation_text ILIKE '%ce mois-ci%'    THEN 'this_month'
            ELSE urgency END
        WHERE urgency IS NULL
          AND recommendation_text IS NOT NULL;
    """)

    # 2. Propage urgence + priorité aux BC créés avant le fix (po.urgency IS NULL).
    op.execute("""
        UPDATE supply.purchase_orders po
        SET urgency  = r.urgency,
            priorite = CASE r.urgency
                WHEN 'immediate'  THEN 'URGENTE'
                WHEN 'this_week'  THEN 'HAUTE'
                WHEN 'this_month' THEN 'NORMAL'
                WHEN 'none'       THEN 'BASSE'
                ELSE 'NORMAL' END
        FROM inventory.recommendations r
        WHERE po.recommendation_id = r.id
          AND po.urgency IS NULL
          AND r.urgency IS NOT NULL;
    """)


def downgrade() -> None:
    # Backfill de données : pas de retour arrière déterministe (on ne sait plus
    # quelles lignes étaient NULL avant). No-op volontaire.
    pass
