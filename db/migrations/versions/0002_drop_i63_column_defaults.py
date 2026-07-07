"""Retire les DEFAULT 'I63' hardcodés sur store_id.

Tous les chemins d'INSERT du code passent store_id explicitement (audit
2026-07-07 : agent_logger, coach/tools, supervisor_agent, coaching_scripts).
Le default masquait des bugs multi-boutiques — un INSERT qui oublie store_id
doit désormais échouer (NOT NULL absent ici, mais plus de valeur silencieuse).

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_TABLES = [
    "public.agent_logs",
    "public.agent_cycles",
    "public.agent_errors",
    "public.rag_feedback",
    "public.coach_interactions",
    "sales.coaching_scripts",
]


def upgrade() -> None:
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} ALTER COLUMN store_id DROP DEFAULT")


def downgrade() -> None:
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} ALTER COLUMN store_id SET DEFAULT 'I63'")
