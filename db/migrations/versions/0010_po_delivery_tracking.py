"""Suivi livraison + auto-confirmation des bons de commande.

Trois besoins Kanban que le schéma ne couvrait pas :

1. Horodatage des jalons du cycle de vie — date_soumission et
   date_confirmation n'existaient pas : impossible de savoir depuis quand un
   BC attend en SOUMIS, donc impossible d'auto-confirmer après 24 h.
2. Écart de livraison persisté — ecart_livraison_jours
   (date_livraison_reelle - date_livraison_prevue, positif = retard,
   négatif = avance) calculé à la réception et stocké, pas seulement dérivé
   à l'affichage. livraison_conforme existait déjà mais n'était jamais écrit.
3. confirmed_auto — distingue une confirmation humaine (drag Kanban) d'une
   confirmation automatique par la boucle 24 h (po_auto_confirm.py), pour
   l'audit et le badge UI.

Backfill : les BC déjà reçus avec les deux dates renseignées récupèrent leur
écart et leur conformité ; les BC actuellement SOUMIS héritent de updated_at
comme date_soumission (meilleure approximation disponible — c'est le moment
de leur dernière transition).

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE supply.purchase_orders
            ADD COLUMN IF NOT EXISTS date_soumission timestamp,
            ADD COLUMN IF NOT EXISTS date_confirmation timestamp,
            ADD COLUMN IF NOT EXISTS confirmed_auto boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS ecart_livraison_jours integer;

        COMMENT ON COLUMN supply.purchase_orders.date_soumission
            IS 'Passage en SOUMIS — base du délai d''auto-confirmation 24h';
        COMMENT ON COLUMN supply.purchase_orders.date_confirmation
            IS 'Passage en CONFIRME (humain ou automatique)';
        COMMENT ON COLUMN supply.purchase_orders.confirmed_auto
            IS 'true = confirmé automatiquement après 24h en SOUMIS sans action humaine';
        COMMENT ON COLUMN supply.purchase_orders.ecart_livraison_jours
            IS 'date_livraison_reelle - date_livraison_prevue : >0 retard, <0 avance, 0 à temps';

        -- Backfill BC déjà reçus : écart et conformité recalculables.
        UPDATE supply.purchase_orders
        SET ecart_livraison_jours = date_livraison_reelle - date_livraison_prevue,
            livraison_conforme    = (date_livraison_reelle <= date_livraison_prevue)
        WHERE date_livraison_reelle IS NOT NULL
          AND date_livraison_prevue IS NOT NULL
          AND ecart_livraison_jours IS NULL;

        -- Backfill BC en attente : updated_at = dernière transition = passage en SOUMIS.
        UPDATE supply.purchase_orders
        SET date_soumission = updated_at
        WHERE statut = 'SOUMIS' AND date_soumission IS NULL;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE supply.purchase_orders
            DROP COLUMN IF EXISTS date_soumission,
            DROP COLUMN IF EXISTS date_confirmation,
            DROP COLUMN IF EXISTS confirmed_auto,
            DROP COLUMN IF EXISTS ecart_livraison_jours;
    """)
