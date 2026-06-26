"""create context_adjustments table

Revision ID: 002
Revises: 001
Create Date: 2026-05-21
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.execute("""
        CREATE TABLE inventory.context_adjustments (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

            sku VARCHAR(50) NOT NULL
                REFERENCES inventory.products(sku),

            store_id VARCHAR(50) NOT NULL
                REFERENCES inventory.stores(store_id),

            valid_from DATE NOT NULL,
            valid_to DATE NOT NULL,

            demand_uplift_pct NUMERIC(6,2),

            dominant_signal VARCHAR(100),

            confidence NUMERIC(5,4),

            interpretation TEXT,

            agent_run_id UUID
                REFERENCES inventory.agent_runs(id),

            created_at TIMESTAMP DEFAULT NOW(),

            UNIQUE (sku, store_id, valid_from)
        )
    """)

    op.execute("""
        CREATE INDEX idx_context_adjustments_dates
        ON inventory.context_adjustments(valid_from, valid_to)
    """)

    op.execute("""
        CREATE INDEX idx_context_adjustments_store_sku
        ON inventory.context_adjustments(store_id, sku)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inventory.context_adjustments")
