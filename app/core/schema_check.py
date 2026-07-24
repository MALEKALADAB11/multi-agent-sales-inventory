"""
Vérification du schéma au démarrage — remplace les CREATE TABLE runtime.

Le schéma appartient aux migrations Alembic (db/migrations). L'application
ne crée JAMAIS de table : au boot elle vérifie que la base est à la révision
attendue et que les tables critiques existent, sinon elle refuse de démarrer
avec un message actionnable.
"""

import logging
import os

import psycopg2

logger = logging.getLogger(__name__)

# Tables sans lesquelles l'app ne peut pas fonctionner (une par domaine).
_CRITICAL_RELATIONS = [
    "public.app_users",
    "public.agent_cycles",
    "public.coach_interactions",
    "public.hitl_reviews",
    "sales.transactions_rt",
    "sales.produits",
    "sales.boutiques",
    "inventory.stock_levels",
    "inventory.recommendations",
    "supply.purchase_orders",
    "supply.supplier_products",
]


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ooredoo_sales"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def _expected_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        # alembic.ini vit à la racine du repo : app/core/ -> app/ -> racine
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg = Config(os.path.join(repo_root, "alembic.ini"))
        # script_location est relatif au cwd dans le .ini : on l'ancre sur le repo
        cfg.set_main_option(
            "script_location", os.path.join(repo_root, "db", "migrations")
        )
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as e:
        logger.warning("[SCHEMA] Impossible de lire le head Alembic : %s", e)
        return None


def verify_schema() -> None:
    """Lève RuntimeError si la base n'est pas au niveau attendu."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM public.alembic_version")
            row = cur.fetchone()
            current = row[0] if row else None

            head = _expected_head()
            if head and current != head:
                raise RuntimeError(
                    f"Base de données à la révision {current!r}, le code attend "
                    f"{head!r} — exécutez : alembic upgrade head"
                )

            missing = []
            for rel in _CRITICAL_RELATIONS:
                cur.execute("SELECT to_regclass(%s)", (rel,))
                if cur.fetchone()[0] is None:
                    missing.append(rel)
            if missing:
                raise RuntimeError(
                    f"Tables critiques absentes : {', '.join(missing)} — "
                    "base non initialisée ? Exécutez : alembic upgrade head"
                )

        logger.info("[SCHEMA] ✅ Base à la révision %s, %d tables critiques OK",
                    current, len(_CRITICAL_RELATIONS))
    finally:
        conn.close()
