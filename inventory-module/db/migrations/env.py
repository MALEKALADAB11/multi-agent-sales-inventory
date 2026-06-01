from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os
import sys
from pathlib import Path

# Make sure inventory-module root is on path so dotenv can find .env
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

config = context.config

# Read DB URL from .env instead of hardcoding credentials in alembic.ini
db_url = (
    f"postgresql://{os.getenv('DOCKER_DB_USER')}"
    f":{os.getenv('DOCKER_DB_PASSWORD')}"
    f"@{os.getenv('DOCKER_DB_HOST')}"
    f":{os.getenv('DOCKER_DB_PORT', '5432')}"
    f"/{os.getenv('DOCKER_DB_NAME')}"
)
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema="inv",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            include_schemas=True,
            version_table="alembic_version",
            version_table_schema="inv",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
