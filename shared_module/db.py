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


# ── Setup schémas ─────────────────────────────────────────────────────────────

def setup_schemas():
    """
    Crée les 3 schémas + tables de base.
    Idempotent (IF NOT EXISTS partout).
    """
    c = psycopg2.connect(**_DB_KWARGS)
    c.set_client_encoding("UTF8")
    try:
        cur = c.cursor()

        cur.execute(f"""
            CREATE SCHEMA IF NOT EXISTS {config.SCHEMA_SALES};
            CREATE SCHEMA IF NOT EXISTS {config.SCHEMA_INVENTORY};
            CREATE SCHEMA IF NOT EXISTS {config.SCHEMA_MONITORING};
        """)

        # ── SALES ────────────────────────────────────────────────────────
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {config.SCHEMA_SALES}.app_users (
                id            SERIAL PRIMARY KEY,
                user_id       VARCHAR(50) UNIQUE,
                username      VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(64) NOT NULL,
                full_name     VARCHAR(100),
                role          VARCHAR(20) DEFAULT 'vendeur',
                store_id      VARCHAR(10) DEFAULT 'I63',
                store_name    VARCHAR(100),
                initials      VARCHAR(5),
                color         VARCHAR(20),
                advisor_id    VARCHAR(20),
                actif         BOOLEAN DEFAULT TRUE,
                last_login    TIMESTAMP,
                created_at    TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS {config.SCHEMA_SALES}.app_sessions (
                id         SERIAL PRIMARY KEY,
                token      VARCHAR(64) UNIQUE NOT NULL,
                user_id    VARCHAR(50),
                expires_at TIMESTAMP NOT NULL,
                last_used  TIMESTAMP DEFAULT NOW(),
                ip_address VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_token
                ON {config.SCHEMA_SALES}.app_sessions(token);

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_SALES}.coaching_scripts (
                id             SERIAL PRIMARY KEY,
                store_id       VARCHAR(10) DEFAULT 'ALL',
                categorie      VARCHAR(100),
                situation      TEXT,
                action         TEXT,
                produit_cible  VARCHAR(200),
                argument_vente TEXT,
                impact_observe TEXT,
                heure_min      SMALLINT DEFAULT 9,
                heure_max      SMALLINT DEFAULT 20,
                jour_semaine   SMALLINT DEFAULT -1,
                source         VARCHAR(50) DEFAULT 'terrain',
                embedding_id   BIGINT,
                embedded       BOOLEAN DEFAULT FALSE,
                actif          BOOLEAN DEFAULT TRUE,
                created_at     TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_scripts_embedded
                ON {config.SCHEMA_SALES}.coaching_scripts(embedded);

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_SALES}.coach_interactions (
                id             SERIAL PRIMARY KEY,
                advisor_name   VARCHAR(100),
                store_id       VARCHAR(10) DEFAULT 'I63',
                message        TEXT,
                response       TEXT,
                gap_pct        FLOAT,
                urgency        VARCHAR(10),
                rag_used       BOOLEAN DEFAULT FALSE,
                nb_rag_scripts INT DEFAULT 0,
                conseil_type   VARCHAR(30) DEFAULT 'general',
                confidence     FLOAT DEFAULT 0.0,
                created_at     TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_interactions_advisor
                ON {config.SCHEMA_SALES}.coach_interactions(advisor_name);
            CREATE INDEX IF NOT EXISTS idx_interactions_created
                ON {config.SCHEMA_SALES}.coach_interactions(created_at DESC);
        """)

        # ── INVENTORY ────────────────────────────────────────────────────
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {config.SCHEMA_INVENTORY}.stock_levels (
                id            SERIAL PRIMARY KEY,
                store_id      VARCHAR(10) NOT NULL,
                sku           VARCHAR(50) NOT NULL,
                product_name  VARCHAR(200),
                category      VARCHAR(50),
                quantity      INT DEFAULT 0,
                min_stock     INT DEFAULT 5,
                max_stock     INT DEFAULT 100,
                reorder_point INT DEFAULT 10,
                unit_cost     NUMERIC(10,3) DEFAULT 0,
                risk_level    VARCHAR(20) DEFAULT 'low',
                last_updated  TIMESTAMP DEFAULT NOW(),
                UNIQUE(store_id, sku)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_store
                ON {config.SCHEMA_INVENTORY}.stock_levels(store_id);
            CREATE INDEX IF NOT EXISTS idx_stock_risk
                ON {config.SCHEMA_INVENTORY}.stock_levels(risk_level);

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_INVENTORY}.stock_movements (
                id            SERIAL PRIMARY KEY,
                store_id      VARCHAR(10),
                sku           VARCHAR(50),
                movement_type VARCHAR(20),
                quantity      INT,
                unit_cost     NUMERIC(10,3),
                reference     VARCHAR(100),
                created_at    TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_INVENTORY}.stock_alerts (
                id          SERIAL PRIMARY KEY,
                store_id    VARCHAR(10),
                sku         VARCHAR(50),
                alert_type  VARCHAR(30),
                message     TEXT,
                resolved    BOOLEAN DEFAULT FALSE,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)

        # ── MONITORING ───────────────────────────────────────────────────
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.agent_logs (
                id           SERIAL PRIMARY KEY,
                cycle_id     VARCHAR(50),
                store_id     VARCHAR(10) DEFAULT 'I63',
                agent_name   VARCHAR(30),
                node_name    VARCHAR(50),
                status       VARCHAR(20),
                input_state  JSONB,
                output_state JSONB,
                duration_ms  FLOAT,
                error_msg    TEXT,
                metadata     JSONB,
                created_at   TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_logs_cycle
                ON {config.SCHEMA_MONITORING}.agent_logs(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_logs_agent
                ON {config.SCHEMA_MONITORING}.agent_logs(agent_name);
            CREATE INDEX IF NOT EXISTS idx_logs_created
                ON {config.SCHEMA_MONITORING}.agent_logs(created_at DESC);

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.agent_cycles (
                id              SERIAL PRIMARY KEY,
                cycle_id        VARCHAR(50) UNIQUE,
                store_id        VARCHAR(10) DEFAULT 'I63',
                triggered_by    VARCHAR(20),
                urgency_level   VARCHAR(10),
                urgency_score   FLOAT,
                gap_pct         FLOAT,
                gap_amount      FLOAT,
                ca_today        FLOAT,
                ca_target       FLOAT,
                forecast_eod    FLOAT,
                analyst_summary TEXT,
                strategie       TEXT,
                nb_actions      INT DEFAULT 0,
                cause_racine    TEXT,
                rag_used        BOOLEAN DEFAULT FALSE,
                nb_rag_scripts  INT DEFAULT 0,
                weather_label   VARCHAR(50),
                weather_effect  FLOAT,
                total_ms        FLOAT,
                nodes_executed  INT DEFAULT 0,
                errors_count    INT DEFAULT 0,
                status          VARCHAR(20) DEFAULT 'completed',
                created_at      TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_cycles_created
                ON {config.SCHEMA_MONITORING}.agent_cycles(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cycles_store
                ON {config.SCHEMA_MONITORING}.agent_cycles(store_id);

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.agent_errors (
                id            SERIAL PRIMARY KEY,
                cycle_id      VARCHAR(50),
                store_id      VARCHAR(10) DEFAULT 'I63',
                agent_name    VARCHAR(30),
                node_name     VARCHAR(50),
                error_type    VARCHAR(50),
                error_msg     TEXT,
                traceback_txt TEXT,
                context       JSONB,
                resolved      BOOLEAN DEFAULT FALSE,
                created_at    TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_errors_resolved
                ON {config.SCHEMA_MONITORING}.agent_errors(resolved);
            CREATE INDEX IF NOT EXISTS idx_errors_created
                ON {config.SCHEMA_MONITORING}.agent_errors(created_at DESC);

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.rag_feedback (
                id           SERIAL PRIMARY KEY,
                cycle_id     VARCHAR(50),
                store_id     VARCHAR(10) DEFAULT 'I63',
                agent_name   VARCHAR(30) DEFAULT 'stratege',
                query        TEXT,
                nb_results   INT,
                top_category VARCHAR(100),
                top_score    FLOAT,
                action_used  TEXT,
                was_useful   BOOLEAN,
                context      JSONB,
                created_at   TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.agent_memory (
                id          SERIAL PRIMARY KEY,
                agent_name  VARCHAR(30),
                store_id    VARCHAR(10),
                cycle_id    VARCHAR(50),
                memory_type VARCHAR(30),
                memory_data JSONB,
                created_at  TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.strategy_memory (
                id               SERIAL PRIMARY KEY,
                cycle_id         VARCHAR(50),
                store_id         VARCHAR(10),
                urgency          VARCHAR(10),
                gap_pct          NUMERIC(6,2),
                forecast_eod     NUMERIC(12,2),
                detected_context JSONB,
                selected_actions JSONB,
                rag_sources      JSONB,
                confidence_score NUMERIC(4,2),
                llm_summary      TEXT,
                created_at       TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS {config.SCHEMA_MONITORING}.inventory_logs (
                id          SERIAL PRIMARY KEY,
                cycle_id    VARCHAR(50),
                store_id    VARCHAR(10),
                sku         VARCHAR(50),
                action      VARCHAR(50),
                details     JSONB,
                duration_ms FLOAT,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)

        c.commit()
        logger.info(
            f"[DB] ✅ Schémas créés : "
            f"{config.SCHEMA_SALES} | "
            f"{config.SCHEMA_INVENTORY} | "
            f"{config.SCHEMA_MONITORING}"
        )
    except Exception as e:
        c.rollback()
        logger.error(f"[DB] setup_schemas: {e}")
        raise
    finally:
        c.close()


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
