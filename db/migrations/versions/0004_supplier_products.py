"""Référentiel fournisseur↔produit (catalogue de sourcing).

Comble le gap backlog ligne 81 : aucun mapping fournisseur→SKU n'existait,
la sélection fournisseur d'un bon de commande n'avait aucun référentiel.
Un seul fournisseur préféré par SKU (index unique partiel).

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE supply.supplier_products (
            id             SERIAL PRIMARY KEY,
            supplier_id    VARCHAR(30) NOT NULL
                           REFERENCES supply.suppliers(supplier_id),
            sku            INTEGER NOT NULL
                           REFERENCES sales.produits(sku),
            lead_time_days INTEGER,
            moq            INTEGER,
            unit_cost      NUMERIC(12,3),
            devise         VARCHAR(5) DEFAULT 'TND',
            is_preferred   BOOLEAN NOT NULL DEFAULT FALSE,
            actif          BOOLEAN NOT NULL DEFAULT TRUE,
            created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (supplier_id, sku)
        );
        CREATE UNIQUE INDEX uq_supplier_products_preferred
            ON supply.supplier_products (sku) WHERE is_preferred;
        CREATE INDEX idx_supplier_products_sku
            ON supply.supplier_products (sku);
        COMMENT ON TABLE supply.supplier_products IS
            'Catalogue de sourcing : quel fournisseur fournit quel SKU '
            '(lead time, MOQ, coût). Un seul is_preferred par SKU.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE supply.supplier_products")
