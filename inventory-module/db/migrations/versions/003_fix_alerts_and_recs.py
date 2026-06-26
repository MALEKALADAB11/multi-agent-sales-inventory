"""Fix alerts status constraint (add rejected), add decided_by,
   fix alert_type constraint (add below_minimum).

Revision ID: 003
Revises: 002
"""
from alembic import op

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── inventory.alerts: add decided_by column ────────────────────────────────────
    op.execute("""
        ALTER TABLE inventory.alerts
        ADD COLUMN IF NOT EXISTS decided_by VARCHAR(100)
    """)

    # ── inventory.alerts: add 'rejected' and 'acknowledged' to status constraint ───
    # Drop old constraint, recreate with full set.
    op.execute("""
        ALTER TABLE inventory.alerts
        DROP CONSTRAINT IF EXISTS alerts_status_check
    """)
    op.execute("""
        ALTER TABLE inventory.alerts
        ADD CONSTRAINT alerts_status_check
        CHECK (status IN (
            'pending', 'acknowledged', 'validated',
            'rejected', 'dismissed', 'resolved'
        ))
    """)

    # ── inventory.alerts: add 'below_minimum' to alert_type constraint ─────────────
    # routes.py _build_alerts() uses 'below_minimum' for high-risk items.
    op.execute("""
        ALTER TABLE inventory.alerts
        DROP CONSTRAINT IF EXISTS alerts_alert_type_check
    """)
    op.execute("""
        ALTER TABLE inventory.alerts
        ADD CONSTRAINT alerts_alert_type_check
        CHECK (alert_type IN (
            'stockout_critical', 'stockout_risk',
            'below_minimum', 'overstock', 'slow_moving'
        ))
    """)

    # ── inventory.recommendations: columns already exist per schema ────────────────
    # status, decided_by, decided_at are already in 001 — nothing to add.
    # Guard with IF NOT EXISTS anyway for safety.
    op.execute("""
        ALTER TABLE inventory.recommendations
        ADD COLUMN IF NOT EXISTS decided_by VARCHAR(100)
    """)
    op.execute("""
        ALTER TABLE inventory.recommendations
        ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP
    """)


def downgrade() -> None:
    # Revert alert_type constraint
    op.execute("""
        ALTER TABLE inventory.alerts
        DROP CONSTRAINT IF EXISTS alerts_alert_type_check
    """)
    op.execute("""
        ALTER TABLE inventory.alerts
        ADD CONSTRAINT alerts_alert_type_check
        CHECK (alert_type IN (
            'stockout_critical', 'stockout_risk',
            'overstock', 'slow_moving'
        ))
    """)

    # Revert status constraint
    op.execute("""
        ALTER TABLE inventory.alerts
        DROP CONSTRAINT IF EXISTS alerts_status_check
    """)
    op.execute("""
        ALTER TABLE inventory.alerts
        ADD CONSTRAINT alerts_status_check
        CHECK (status IN ('pending', 'validated', 'dismissed', 'resolved'))
    """)

    # Drop decided_by (can't un-add a column safely without data loss — skip)
