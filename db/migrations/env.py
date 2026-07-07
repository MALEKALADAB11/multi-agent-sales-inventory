"""
Alembic env — arbre de migrations unique du projet.

URL de connexion :
  1. ALEMBIC_DB_URL si défini (utilisé pour tester sur une base scratch)
  2. sinon construite depuis .env : DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD

Projet raw-SQL : pas d'ORM, target_metadata=None, migrations écrites à la main.
Table de version : public.alembic_version.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from dotenv import load_dotenv

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _db_url() -> str:
    override = os.getenv("ALEMBIC_DB_URL")
    if override:
        return override
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "ooredoo_sales")
    user = os.getenv("DB_USER", "postgres")
    pwd = os.getenv("DB_PASSWORD", "")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{name}"


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="public",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_db_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema="public",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
