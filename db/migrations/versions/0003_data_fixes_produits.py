"""Corrige les libellés produit corrompus + vocabulaire statut PO.

- sales.transactions_rt.des_produit contenait le SKU brut au lieu du nom
  produit sur 8 357 lignes / 8 448 (bug du simulateur temps réel, backlog
  ligne 78). Réparé par jointure sales.produits sur cod_prod.
- CHECK sur supply.purchase_orders.statut aligné sur ALLOWED_TRANSITIONS
  du supply_repo (9 statuts). Données existantes : ANNULE, CONFIRME — OK.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_STATUTS = (
    "'SUGGERE','BROUILLON','SOUMIS','CONFIRME','EXPEDIE',"
    "'RECU_PARTIEL','RECU','ANNULE','LITIGE'"
)


def upgrade() -> None:
    op.execute("""
        UPDATE sales.transactions_rt t
        SET des_produit = p.nom
        FROM sales.produits p
        WHERE p.sku = t.cod_prod
          AND (t.des_produit ~ '^[0-9]+$' OR t.des_produit IS NULL)
    """)
    op.execute(f"""
        ALTER TABLE supply.purchase_orders
        ADD CONSTRAINT ck_po_statut CHECK (statut IN ({_STATUTS}))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE supply.purchase_orders DROP CONSTRAINT ck_po_statut")
    # La réparation des libellés n'est pas réversible (l'ancien contenu était
    # le SKU, information toujours présente dans cod_prod).
