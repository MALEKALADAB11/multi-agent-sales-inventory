"""
setup_db.py — Initialisation complète de la base de données.
À exécuter UNE SEULE FOIS (ou idempotent si relancé).

Usage :
    python setup_db.py
    python setup_db.py --migrate-sales    # migrer tables sales du schéma public
    python setup_db.py --insert-users     # insérer comptes par défaut
"""

import sys
import logging
import psycopg2
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Import shared_module
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared_module.config import config
from shared_module.db import setup_schemas, get_conn


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def migrate_from_public():
    """
    Migre les tables existantes du schéma public vers les nouveaux schémas.
    Utile si vous avez déjà des données dans le schéma public.
    """
    logger.info("=== Migration schéma public → schémas séparés ===")

    tables_sales = [
        "app_users", "app_sessions", "coaching_scripts",
        "coach_interactions", "agents", "transactions",
    ]
    tables_monitoring = [
        "agent_logs", "agent_cycles", "agent_errors",
        "rag_feedback",
    ]

    with get_conn("public") as conn:
        with conn.cursor() as cur:
            # Vérifier quelles tables existent dans public
            cur.execute("""
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
            """)
            existing = {r[0] for r in cur.fetchall()}

            for table in tables_sales:
                if table in existing:
                    try:
                        cur.execute(f"""
                            INSERT INTO {config.SCHEMA_SALES}.{table}
                            SELECT * FROM public.{table}
                            ON CONFLICT DO NOTHING
                        """)
                        logger.info(f"  ✓ public.{table} → {config.SCHEMA_SALES}.{table}")
                    except Exception as e:
                        logger.warning(f"  ⚠ {table}: {e}")

            for table in tables_monitoring:
                if table in existing:
                    try:
                        cur.execute(f"""
                            INSERT INTO {config.SCHEMA_MONITORING}.{table}
                            SELECT * FROM public.{table}
                            ON CONFLICT DO NOTHING
                        """)
                        logger.info(f"  ✓ public.{table} → {config.SCHEMA_MONITORING}.{table}")
                    except Exception as e:
                        logger.warning(f"  ⚠ {table}: {e}")

    logger.info("Migration terminée.")


def insert_default_users():
    """Insère les comptes par défaut."""
    users = [
        # (username, password, full_name, role, store_id, store_name)
        ("managerlac2",    "admin123",  "Manager Ghassen",       "manager", "I63", "FR LAC2 Tunisia Mall"),
        ("zouiTeninsaf",   "zi1234",    "Zouiten Insaf",         "vendeur", "I63", "FR LAC2 Tunisia Mall"),
        ("mansourHela",    "mh1234",    "Mansour Hela",           "vendeur", "I63", "FR LAC2 Tunisia Mall"),
        ("benammarMeriam", "bm1234",    "Ben Ammar Meriam",       "vendeur", "I63", "FR LAC2 Tunisia Mall"),
        ("mansourKhouloud","mk1234",    "Mansour Khouloud",       "vendeur", "I63", "FR LAC2 Tunisia Mall"),
        ("admin",          "admin123",  "Administrateur système", "admin",   "I63", "Siège"),
    ]

    inserted = 0
    with get_conn(config.SCHEMA_SALES) as conn:
        with conn.cursor() as cur:
            for username, password, full_name, role, store_id, store_name in users:
                try:
                    cur.execute(f"""
                        INSERT INTO {config.SCHEMA_SALES}.app_users
                            (username, password_hash, full_name, role,
                             store_id, store_name, actif)
                        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (username) DO NOTHING
                    """, (username, sha256(password), full_name,
                          role, store_id, store_name))
                    if cur.rowcount > 0:
                        logger.info(f"  ✓ User créé: {username} ({role})")
                        inserted += 1
                except Exception as e:
                    logger.warning(f"  ⚠ {username}: {e}")

    logger.info(f"{inserted} utilisateurs créés.")


def verify():
    """Vérification finale."""
    logger.info("\n=== Vérification ===")
    schemas = [config.SCHEMA_SALES, config.SCHEMA_INVENTORY, config.SCHEMA_MONITORING]

    with get_conn("public") as conn:
        with conn.cursor() as cur:
            for schema in schemas:
                cur.execute("""
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = %s ORDER BY tablename
                """, (schema,))
                tables = [r[0] for r in cur.fetchall()]
                logger.info(f"  {schema}: {len(tables)} tables — {tables}")

            # Compter les données
            try:
                cur.execute(f"SELECT COUNT(*) FROM {config.SCHEMA_SALES}.app_users")
                logger.info(f"  Users: {cur.fetchone()[0]}")
            except Exception:
                pass

            try:
                cur.execute(f"SELECT COUNT(*) FROM {config.SCHEMA_SALES}.coaching_scripts")
                logger.info(f"  Scripts RAG: {cur.fetchone()[0]}")
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║  SETUP DB — AI Sales Coach Ooredoo               ║")
    logger.info(f"║  DB: {config.DB_NAME} @ {config.DB_HOST}:{config.DB_PORT}  ║")
    logger.info("╚══════════════════════════════════════════════════╝")

    # 1. Créer les schémas et tables
    logger.info("\n=== 1. Création des schémas ===")
    setup_schemas()

    # 2. Migrer depuis public si demandé
    if "--migrate-sales" in sys.argv:
        logger.info("\n=== 2. Migration depuis schéma public ===")
        migrate_from_public()

    # 3. Insérer les utilisateurs par défaut
    if "--insert-users" in sys.argv or True:  # toujours
        logger.info("\n=== 3. Utilisateurs par défaut ===")
        insert_default_users()

    # 4. Vérification
    verify()

    logger.info("\n✅ Setup terminé !")
    logger.info(
        f"Connexion : postgresql://{config.DB_USER}:***"
        f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
    )
    logger.info(f"Schémas   : {config.SCHEMA_SALES} | "
                f"{config.SCHEMA_INVENTORY} | {config.SCHEMA_MONITORING}")