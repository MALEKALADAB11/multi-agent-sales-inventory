"""Add missing columns to alerts table: recommended_action, agent_run_id.

Revision ID: 004
Revises: 003
Create Date: 2026-06-22
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inventory.alerts
        ADD COLUMN IF NOT EXISTS recommended_action TEXT
    """)

    op.execute("""
        ALTER TABLE inventory.alerts
        ADD COLUMN IF NOT EXISTS agent_run_id TEXT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE inventory.alerts DROP COLUMN IF EXISTS agent_run_id")
    op.execute("ALTER TABLE inventory.alerts DROP COLUMN IF EXISTS recommended_action")
