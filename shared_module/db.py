"""
shared_module/db.py — Gestionnaire de connexions PostgreSQL centralisé.
Une seule DB ooredoo_sales, 3 schémas : sales | inventory | monitoring
"""

import logging
import json
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

from .config import config

logger = logging.getLogger(__name__)

_DB_KWARGS = dict(
    host     = config.DB_HOST,
    port     = config.DB_PORT,
    dbname   = config.DB_NAME,
    user     = config.DB_USER,
    password = config.DB_PASSWORD,
)

_sync_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=20, **_DB_KWARGS
        )
    return _sync_pool


@contextmanager
def get_conn(schema: str = "public"):
    """
    Context manager psycopg2 avec search_path automatique.
    Commit auto en sortie, rollback sur exception.

    Usage :
        with get_conn("sales") as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM app_users")
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.set_client_encoding("UTF8")
        if schema and schema != "public":
            with conn.cursor() as cur:
                # search_path inclut public en fallback pour compatibilité
                cur.execute(f"SET search_path TO {schema}, public")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def get_raw_conn(schema: str = "public") -> psycopg2.extensions.connection:
    """Connexion directe sans pool — l'appelant gère commit/close."""
    c = psycopg2.connect(**_DB_KWARGS)
    c.set_client_encoding("UTF8")
    if schema and schema != "public":
        with c.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}, public")
    return c


# Raccourcis
def get_sales_conn():      return get_conn(config.SCHEMA_SALES)
def get_inventory_conn():  return get_conn(config.SCHEMA_INVENTORY)
def get_monitoring_conn(): return get_conn(config.SCHEMA_MONITORING)


# ── Pool asyncpg ──────────────────────────────────────────────────────────────
_async_pool = None


async def get_async_pool():
    global _async_pool
    if _async_pool is None:
        try:
            import asyncpg
            _async_pool = await asyncpg.create_pool(
                host     = config.DB_HOST,
                port     = config.DB_PORT,
                database = config.DB_NAME,
                user     = config.DB_USER,
                password = config.DB_PASSWORD,
                min_size = 2,
                max_size = 15,
                command_timeout = 30,
            )
        except Exception as e:
            logger.warning(f"[DB] asyncpg pool: {e}")
    return _async_pool


# Le schema de la base appartient aux migrations Alembic (db/migrations).
# L'ancien setup_schemas() (jamais appele, cibles desynchronisees) est supprime.


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def fetchall(query: str, params=None, schema: str = "public") -> list:
    with get_conn(schema) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]


def fetchone(query: str, params=None, schema: str = "public") -> Optional[dict]:
    with get_conn(schema) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None


def execute(query: str, params=None, schema: str = "public") -> None:
    with get_conn(schema) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)


def execute_returning(query: str, params=None, schema: str = "public"):
    with get_conn(schema) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()[0]
