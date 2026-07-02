"""
run_all_migrations.py
══════════════════════
Exécution ordonnée de toutes les migrations SQL et seeds pour Ooredoo Sales DB.

Ordre d'exécution garanti :
  1. migration_005.sql         — Schémas market, customer, supply
  2. migration_006_kpi.sql     — Tables KPI daily
  3. migration_007_coaching_ops.sql — Tables coaching opérationnelles + vues monitoring
  4. seed_data.sql             — Données de base (boutiques, agents, produits)
  5. seed_market_supply.sql    — Marché, concurrents, fournisseurs
  6. seed_kpis.sql             — KPIs historiques boutique I63
  7. seed_008_promotions.sql   — Promotions été 2026
  8. seed_009_coaching_events.sql — 22 événements coaching I63
  9. seed_010_nps_csat.sql     — 58 feedbacks NPS/CSAT I63

Usage:
    python scripts/run_all_migrations.py [--seeds-only] [--migrations-only] [--dry-run]

Prérequis:
    pip install psycopg2-binary python-dotenv
    DB démarrée avec le schéma initial (migrations 001-004 déjà appliquées).

Notes:
  - Idempotent : ON CONFLICT DO NOTHING / CREATE IF NOT EXISTS partout
  - Migrations 001-004 sont supposées déjà appliquées (structure de base)
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=int(os.getenv("POSTGRES_PORT", 5432)),
    dbname=os.getenv("POSTGRES_DB", "ooredoo_sales"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "admin"),
)

SQL_DIR = ROOT / "data" / "telco"

# ── Ordre d'exécution ─────────────────────────────────────────────────────────
MIGRATIONS = [
    ("migration", "migration_005.sql"),
    ("migration", "migration_006_kpi.sql"),
    ("migration", "migration_007_coaching_ops.sql"),
]

SEEDS = [
    ("seed",      "seed_data.sql"),
    ("seed",      "seed_market_supply.sql"),
    ("seed",      "seed_kpis.sql"),
    ("seed",      "seed_008_promotions.sql"),
    ("seed",      "seed_009_coaching_events.sql"),
    ("seed",      "seed_010_nps_csat.sql"),
]


def get_conn():
    os.environ["PGCLIENTENCODING"] = "UTF8"
    c = psycopg2.connect(**DB)
    c.set_client_encoding("UTF8")
    return c


def execute_sql_file(conn, path: Path, dry_run: bool = False) -> bool:
    if not path.exists():
        log.warning(f"  SKIP (fichier absent) : {path.name}")
        return True

    try:
        sql = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        sql = path.read_text(encoding="latin-1")

    # Retirer les SELECT de vérification en fin de script (évite psycopg2 cursor issues)
    # On garde uniquement les DDL/DML
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().upper().startswith("--")]

    log.info(f"  → {path.name} ({len(statements)} statements)")

    if dry_run:
        log.info(f"    DRY RUN — skip exécution")
        return True

    t0 = time.time()
    try:
        with conn.cursor() as cur:
            # Exécuter tout le fichier SQL d'un coup (meilleure gestion des transactions)
            cur.execute(sql)
        conn.commit()
        elapsed = round((time.time() - t0) * 1000)
        log.info(f"    ✅ OK en {elapsed}ms")
        return True

    except psycopg2.Error as e:
        conn.rollback()
        log.error(f"    ❌ ERREUR PostgreSQL : {e.pgcode} — {e.pgerror}")
        log.error(f"       Fichier : {path.name}")
        return False

    except Exception as e:
        conn.rollback()
        log.error(f"    ❌ ERREUR : {e}")
        return False


def check_db_connection(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_database()")
            ver, db = cur.fetchone()
            log.info(f"  Connecté à : {db}")
            log.info(f"  PostgreSQL  : {ver[:50]}")
        return True
    except Exception as e:
        log.error(f"  Connexion DB échouée : {e}")
        return False


def check_prerequisites(conn) -> dict:
    """Vérifie quels schémas/tables existent déjà."""
    status = {}
    checks = [
        ("schema_sales",     "SELECT 1 FROM information_schema.schemata WHERE schema_name='sales'"),
        ("schema_inventory", "SELECT 1 FROM information_schema.schemata WHERE schema_name='inventory'"),
        ("schema_coaching",  "SELECT 1 FROM information_schema.schemata WHERE schema_name='coaching'"),
        ("schema_market",    "SELECT 1 FROM information_schema.schemata WHERE schema_name='market'"),
        ("table_boutiques",  "SELECT 1 FROM information_schema.tables WHERE table_schema='sales' AND table_name='boutiques'"),
        ("table_agents",     "SELECT 1 FROM information_schema.tables WHERE table_schema='sales' AND table_name='agents'"),
        ("table_transactions","SELECT 1 FROM information_schema.tables WHERE table_schema='sales' AND table_name='transactions'"),
        ("table_stock_levels","SELECT 1 FROM information_schema.tables WHERE table_schema='inventory' AND table_name='stock_levels'"),
    ]
    with conn.cursor() as cur:
        for name, query in checks:
            cur.execute(query)
            status[name] = cur.fetchone() is not None
    return status


def print_status_report(conn) -> None:
    """Affiche un résumé de l'état de la DB après les migrations."""
    log.info("─" * 50)
    log.info("RAPPORT FINAL")
    queries = [
        ("Boutiques",         "SELECT COUNT(*) FROM sales.boutiques"),
        ("Agents",            "SELECT COUNT(*) FROM sales.agents"),
        ("Transactions",      "SELECT COUNT(*) FROM sales.transactions"),
        ("Stock SKUs",        "SELECT COUNT(*) FROM inventory.stock_levels"),
        ("Stock History",     "SELECT COUNT(*) FROM inventory.stock_history"),
        ("Coaching Events",   "SELECT COUNT(*) FROM coaching.coaching_events"),
        ("Market Events",     "SELECT COUNT(*) FROM market.events"),
        ("NPS/CSAT Records",  "SELECT COUNT(*) FROM customer.nps_csat"),
        ("KPI Agent (daily)", "SELECT COUNT(*) FROM agent_kpi_daily"),
        ("Promotions actives","SELECT COUNT(*) FROM inventory.promotions WHERE end_date >= CURRENT_DATE"),
    ]
    with conn.cursor() as cur:
        for label, q in queries:
            try:
                cur.execute(q)
                count = cur.fetchone()[0]
                log.info(f"  {label:<25} : {count:>8,}")
            except Exception:
                log.info(f"  {label:<25} : N/A (table absente)")


def main():
    parser = argparse.ArgumentParser(description="Exécution ordonnée des migrations Ooredoo")
    parser.add_argument("--seeds-only",      action="store_true", help="Seeds uniquement (pas migrations DDL)")
    parser.add_argument("--migrations-only", action="store_true", help="Migrations DDL uniquement (pas seeds)")
    parser.add_argument("--dry-run",         action="store_true", help="Afficher sans exécuter")
    parser.add_argument("--stop-on-error",   action="store_true", help="Arrêter au premier échec")
    args = parser.parse_args()

    log.info("═" * 60)
    log.info("RUNNER DE MIGRATIONS — OOREDOO SALES DB")
    log.info(f"  Host : {DB['host']}:{DB['port']} / {DB['dbname']}")
    log.info(f"  Mode : {'DRY RUN' if args.dry_run else 'EXÉCUTION'}")
    log.info("═" * 60)

    try:
        conn = get_conn()
    except Exception as e:
        log.error(f"Impossible de se connecter à PostgreSQL : {e}")
        log.error(f"Vérifiez vos variables d'environnement : POSTGRES_HOST/PORT/DB/USER/PASSWORD")
        sys.exit(1)

    if not check_db_connection(conn):
        sys.exit(1)

    # Vérification des prérequis
    prereqs = check_prerequisites(conn)
    missing_schemas = [k for k, v in prereqs.items() if k.startswith("schema_") and not v]
    if missing_schemas:
        log.warning(f"  Schémas manquants (migrations 001-004 non appliquées?) : {missing_schemas}")
        log.warning(f"  Continuons quand même — certains scripts créent les schémas")

    # Sélection des fichiers à exécuter
    to_run = []
    if not args.seeds_only:
        to_run.extend(MIGRATIONS)
    if not args.migrations_only:
        to_run.extend(SEEDS)

    log.info(f"\n{len(to_run)} fichiers à exécuter :\n")

    errors = []
    for file_type, filename in to_run:
        path = SQL_DIR / filename
        label = "MIGRATION" if file_type == "migration" else "SEED    "
        log.info(f"[{label}] {filename}")

        ok = execute_sql_file(conn, path, args.dry_run)
        if not ok:
            errors.append(filename)
            if args.stop_on_error:
                log.error(f"Arrêt sur erreur (--stop-on-error)")
                break

    log.info("")

    if not args.dry_run:
        try:
            print_status_report(conn)
        except Exception as e:
            log.warning(f"Rapport final échoué : {e}")

    log.info("═" * 60)
    if errors:
        log.error(f"❌ {len(errors)} fichier(s) en erreur :")
        for f in errors:
            log.error(f"   - {f}")
        sys.exit(1)
    else:
        log.info(f"✅ TOUTES LES MIGRATIONS RÉUSSIES ({len(to_run)} fichiers)")
        log.info("")
        log.info("Prochaines étapes :")
        log.info("  1. python scripts/generate_multistore_data.py --days 90")
        log.info("  2. python scripts/seed_rag_milvus.py")
        log.info("  3. python scripts/embed_coaching_scripts.py")
        log.info("  4. uvicorn main:app --reload")

    conn.close()


if __name__ == "__main__":
    main()
