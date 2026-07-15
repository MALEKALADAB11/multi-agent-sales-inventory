"""
shared_module/db.py — Gestionnaire de connexions PostgreSQL centralisé.
Une seule DB ooredoo_sales, 3 schémas : sales | inventory | monitoring
"""

import asyncio
import logging
import json
import time
import weakref
from contextlib import asynccontextmanager, contextmanager
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
            minconn=2, maxconn=40, connect_timeout=10, **_DB_KWARGS
        )
    return _sync_pool


def getconn(timeout: float = 30.0) -> psycopg2.extensions.connection:
    """
    Connexion du pool partagé, à rendre via putconn().

    ThreadedConnectionPool.getconn() lève PoolError immédiatement quand le
    pool est plein ; ici on attend jusqu'à `timeout` secondes qu'une connexion
    se libère — indispensable pour les batchs inventory très parallèles.
    """
    pool = _get_pool()
    deadline = time.monotonic() + timeout
    while True:
        try:
            return pool.getconn()
        except psycopg2.pool.PoolError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
        except UnicodeDecodeError as e:
            # Serveur dont lc_messages n'est pas UTF-8 (ex: French_France.1252) :
            # psycopg2 ne peut pas décoder le message d'échec de connexion et
            # masque la vraie cause (typiquement "too many clients already").
            raise psycopg2.OperationalError(
                "connexion PostgreSQL refusée (message serveur non décodable — "
                "probablement max_connections atteint ; voir lc_messages)"
            ) from e


def putconn(conn) -> None:
    """Rend au pool partagé une connexion obtenue via getconn()."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


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
    conn = getconn()
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
        putconn(conn)


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
#
# Un pool asyncpg est lie a la boucle d'evenements qui l'a cree : le reutiliser
# depuis une autre boucle leve une erreur. Or ce projet fait tourner plusieurs
# boucles (uvicorn, plus les asyncio.run() des wrappers MCP et du bus d'alertes
# Redis, executes dans des threads). Les pools sont donc indexes par boucle, via
# une WeakKeyDictionary pour qu'ils disparaissent avec elle.

_ASYNC_POOL_KWARGS = dict(
    host     = config.DB_HOST,
    port     = config.DB_PORT,
    database = config.DB_NAME,
    user     = config.DB_USER,
    password = config.DB_PASSWORD,
)

_async_pools: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_async_pool_locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _live_pool_for_current_loop():
    """Pool deja ouvert pour la boucle courante, sinon None. Ne cree rien."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    pool = _async_pools.get(loop)
    if pool is None or pool.is_closing():
        return None
    return pool


async def get_async_pool():
    """Pool de la boucle courante, cree a la demande. None si la DB est injoignable."""
    loop = asyncio.get_running_loop()
    pool = _live_pool_for_current_loop()
    if pool is not None:
        return pool

    lock = _async_pool_locks.get(loop)
    if lock is None:
        lock = _async_pool_locks[loop] = asyncio.Lock()

    async with lock:
        # Un autre appel concurrent a pu ouvrir le pool pendant l'attente du verrou.
        pool = _live_pool_for_current_loop()
        if pool is not None:
            return pool
        try:
            import asyncpg
            pool = await asyncpg.create_pool(
                **_ASYNC_POOL_KWARGS,
                min_size = 2,
                max_size = 15,
                command_timeout = 30,
            )
        except Exception as e:
            logger.warning(f"[DB] asyncpg pool: {e}")
            return None
        _async_pools[loop] = pool
        return pool


async def close_async_pool() -> None:
    """Ferme proprement le pool de la boucle courante (arret applicatif)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    pool = _async_pools.pop(loop, None)
    if pool is not None and not pool.is_closing():
        await pool.close()


async def acquire_conn(connect_timeout: float = 10.0):
    """
    Variante bas niveau de acquire(), pour les blocs try/except/finally deja en
    place qu'un `async with` obligerait a reindenter. L'appelant DOIT rendre la
    connexion via release_conn() dans un finally.
    """
    pool = _live_pool_for_current_loop()
    if pool is not None:
        return await pool.acquire()

    import asyncpg
    return await asyncpg.connect(**_ASYNC_POOL_KWARGS, timeout=connect_timeout)


async def release_conn(conn) -> None:
    """Rend la connexion au pool, ou la ferme si elle a ete ouverte en direct."""
    import asyncpg.pool
    pool = _live_pool_for_current_loop()
    if pool is not None and isinstance(conn, asyncpg.pool.PoolConnectionProxy):
        await pool.release(conn)
    else:
        await conn.close()


@asynccontextmanager
async def acquire(connect_timeout: float = 10.0):
    """
    Connexion asyncpg pour la boucle courante.

    Prend une connexion du pool quand il existe (cas normal : requetes servies par
    uvicorn). Sur une boucle ephemere (asyncio.run dans un thread), ouvrir un pool
    ne servirait a rien et fuirait des connexions a la mort de la boucle : on
    retombe alors sur une connexion directe, refermee en sortie.

        async with acquire() as conn:
            row = await conn.fetchrow("SELECT 1")
    """
    pool = _live_pool_for_current_loop()
    if pool is not None:
        async with pool.acquire() as conn:
            yield conn
        return

    import asyncpg
    conn = await asyncpg.connect(**_ASYNC_POOL_KWARGS, timeout=connect_timeout)
    try:
        yield conn
    finally:
        await conn.close()


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
