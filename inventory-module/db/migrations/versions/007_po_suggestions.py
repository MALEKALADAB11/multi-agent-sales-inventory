"""Add SUGGERE status + agent traceability to purchase_orders.

Revision ID: 007
Revises: 006
Create Date: 2026-07-06

Note: this project's live databases have historically drifted from the
alembic migration chain (see docs/BACKLOG.tsv "Backlog Dette" — multiple
divergent migration configs). inventory.recommendations already has
urgency/confidence columns on live DBs even though migration 001 never
defined them, and supply.purchase_orders is missing agent_decision_id even
though migration 006 defined it. Every ADD COLUMN here is therefore
IF NOT EXISTS so this migration is safe to run regardless of which of those
two states a given database is in.
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── supply.purchase_orders: allow SUGGERE + agent traceability columns ─────
    op.execute("ALTER TABLE supply.purchase_orders DROP CONSTRAINT IF EXISTS purchase_orders_statut_check")
    op.execute("""
        ALTER TABLE supply.purchase_orders
        ADD CONSTRAINT purchase_orders_statut_check
        CHECK (statut IN (
            'SUGGERE', 'BROUILLON', 'SOUMIS', 'CONFIRME', 'EXPEDIE',
            'RECU_PARTIEL', 'RECU', 'ANNULE', 'LITIGE'
        ))
    """)
    op.execute("""
        ALTER TABLE supply.purchase_orders
        ADD COLUMN IF NOT EXISTS source VARCHAR(10) NOT NULL DEFAULT 'MANUEL',
        ADD COLUMN IF NOT EXISTS urgency VARCHAR(20),
        ADD COLUMN IF NOT EXISTS confidence NUMERIC(5, 4),
        ADD COLUMN IF NOT EXISTS agent_decision_id UUID
    """)
    op.execute("ALTER TABLE supply.purchase_orders DROP CONSTRAINT IF EXISTS purchase_orders_source_check")
    op.execute("""
        ALTER TABLE supply.purchase_orders
        ADD CONSTRAINT purchase_orders_source_check CHECK (source IN ('AGENT', 'MANUEL'))
    """)

    # ── inventory.recommendations: persist urgency (present on some live DBs
    # already, absent on a clean run of migration 001 — make it explicit) ─────
    op.execute("ALTER TABLE inventory.recommendations ADD COLUMN IF NOT EXISTS urgency VARCHAR(20)")


def downgrade() -> None:
    op.execute("""
        ALTER TABLE supply.purchase_orders
        DROP COLUMN IF EXISTS source,
        DROP COLUMN IF EXISTS urgency,
        DROP COLUMN IF EXISTS confidence,
        DROP COLUMN IF EXISTS agent_decision_id
    """)
    op.execute("ALTER TABLE supply.purchase_orders DROP CONSTRAINT IF EXISTS purchase_orders_statut_check")
    op.execute("""
        ALTER TABLE supply.purchase_orders
        ADD CONSTRAINT purchase_orders_statut_check
        CHECK (statut IN (
            'BROUILLON', 'SOUMIS', 'CONFIRME', 'EXPEDIE',
            'RECU_PARTIEL', 'RECU', 'ANNULE', 'LITIGE'
        ))
    """)
