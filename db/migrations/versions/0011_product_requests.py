"""Demandes de réapprovisionnement des conseillers.

Ferme la boucle terrain → manager : un conseiller (role=vendeur) qui voit une
alerte rupture/stock critique sur sa page peut créer une demande sur le
produit ; le manager la voit dans son espace de supervision et l'approuve ou
la rejette. Une demande approuvée peut ensuite être reliée à un bon de
commande (po_id) créé sur le Kanban Achats.

Statuts : EN_ATTENTE → APPROUVEE | REJETEE (décision manager, horodatée).

Revision ID: 0011
Revises: 0010
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.product_requests (
            request_id    varchar(40)  PRIMARY KEY,
            store_id      varchar(20)  NOT NULL,
            sku           varchar(40),
            product_name  varchar(150) NOT NULL,
            quantity      integer      NOT NULL CHECK (quantity > 0),
            reason        text,
            urgency       varchar(15)  NOT NULL DEFAULT 'NORMALE'
                          CHECK (urgency IN ('RUPTURE','CRITIQUE','NORMALE')),
            statut        varchar(15)  NOT NULL DEFAULT 'EN_ATTENTE'
                          CHECK (statut IN ('EN_ATTENTE','APPROUVEE','REJETEE')),
            requested_by  varchar(30)  NOT NULL
                          REFERENCES public.app_users(user_id),
            advisor_name  varchar(100),
            manager_note  text,
            decided_by    varchar(30)  REFERENCES public.app_users(user_id),
            decided_at    timestamp,
            po_id         varchar(40),
            created_at    timestamp    NOT NULL DEFAULT now(),
            updated_at    timestamp    NOT NULL DEFAULT now()
        );

        COMMENT ON TABLE public.product_requests
            IS '[SUPPLY] Demandes de réappro émises par les conseillers depuis les alertes rupture — porte HITL manager avant tout BC.';
        COMMENT ON COLUMN public.product_requests.urgency
            IS 'Copie du niveau d''alerte à la création : RUPTURE (stock 0), CRITIQUE (stock critique), NORMALE';
        COMMENT ON COLUMN public.product_requests.po_id
            IS 'Bon de commande supply.purchase_orders relié après approbation (optionnel)';

        CREATE INDEX IF NOT EXISTS idx_product_requests_store_statut
            ON public.product_requests (store_id, statut, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_product_requests_requested_by
            ON public.product_requests (requested_by, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.product_requests;")
