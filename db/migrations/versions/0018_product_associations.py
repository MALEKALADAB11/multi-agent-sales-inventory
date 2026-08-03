"""Product associations for complementary/cross-sell recommendations.

Stores computed rules from temporal correlation analysis of sales_history.
When product A is sold, products B, C, D are often sold on the same day/store.

Revision ID: 0018
Revises: 0017
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS inventory.product_associations (
            id                  SERIAL PRIMARY KEY,
            sku1                integer,
            sku2                integer,
            gamme1              text         NOT NULL,
            gamme2              text         NOT NULL,
            co_occurrence_days integer      NOT NULL,
            confidence          numeric(10,6) NOT NULL,  -- P(sku2 | sku1)
            lift                numeric(12,6),          -- lift > 1 means positive association
            support             numeric(10,6) NOT NULL,  -- P(sku1 AND sku2)
            last_updated        timestamp    NOT NULL DEFAULT now(),
            store_id            text,                     -- NULL = global, text = store-specific
            CONSTRAINT uq_product_assoc UNIQUE (sku1, sku2, store_id)
        );

        COMMENT ON TABLE inventory.product_associations
            IS '[CROSS-SELL] Règles d''association produits calculées depuis sales_history (même jour/magasin).';
        COMMENT ON COLUMN inventory.product_associations.sku1
            IS 'Produit source (celui qu''on recommande de pousser). NULL pour une règle de niveau gamme.';
        COMMENT ON COLUMN inventory.product_associations.sku2
            IS 'Produit complémentaire recommandé. NULL pour une règle de niveau gamme.';
        COMMENT ON COLUMN inventory.product_associations.confidence
            IS 'Confiance: P(sku2 | sku1) = probabilité que sku2 soit vendu si sku1 est vendu.';
        COMMENT ON COLUMN inventory.product_associations.lift
            IS 'Lift > 1: association positive (plus souvent qu''au hasard). Lift < 1: négative.';
        COMMENT ON COLUMN inventory.product_associations.support
            IS 'Support: fréquence de co-occurrence dans toutes les transactions.';
        COMMENT ON COLUMN inventory.product_associations.store_id
            IS 'NULL = règle globale (tous magasins). Text = règle spécifique à un magasin.';

        CREATE INDEX IF NOT EXISTS idx_product_assoc_sku1
            ON inventory.product_associations (sku1);
        CREATE INDEX IF NOT EXISTS idx_product_assoc_sku2
            ON inventory.product_associations (sku2);
        CREATE INDEX IF NOT EXISTS idx_product_assoc_gamme1
            ON inventory.product_associations (gamme1);
        CREATE INDEX IF NOT EXISTS idx_product_assoc_gamme2
            ON inventory.product_associations (gamme2);
        CREATE INDEX IF NOT EXISTS idx_product_assoc_confidence
            ON inventory.product_associations (confidence DESC);

        -- If the table already existed from a previous run (created before
        -- sku1/sku2 were made nullable), relax the constraint now.
        ALTER TABLE inventory.product_associations ALTER COLUMN sku1 DROP NOT NULL;
        ALTER TABLE inventory.product_associations ALTER COLUMN sku2 DROP NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inventory.product_associations;")