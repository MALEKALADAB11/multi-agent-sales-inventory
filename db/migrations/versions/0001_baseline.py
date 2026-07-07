"""Baseline — état complet de la base ooredoo_sales au 2026-07-07.

Générée par introspection (pg_dump --schema-only) de la base vivante :
8 schémas, 50 tables, 15 vues, 4 fonctions, triggers, extension uuid-ossp.

⚠️ Cette révision n'est JAMAIS exécutée sur la base vivante : celle-ci a été
marquée `alembic stamp 0001` (le schéma existait déjà). Elle sert à
reconstruire une base fraîche à l'identique : `alembic upgrade head`
sur une base vide.

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SQL = Path(__file__).parent / "sql" / "0001_baseline.sql"


def upgrade() -> None:
    op.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Le baseline ne se downgrade pas — restaurer un dump si nécessaire."
    )
