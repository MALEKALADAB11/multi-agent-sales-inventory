"""
migrate_to_schemas.py — Migration complète public → sales|inventory|monitoring
Migre toutes les données existantes colonne par colonne (sans erreur de schéma).
Idempotent : peut être relancé sans problème.

Usage :
    python migrate_to_schemas.py
    python migrate_to_schemas.py --dry-run   (voir sans exécuter)
"""

import sys
import logging
import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DB = dict(host="localhost", port=5432, dbname="ooredoo_sales",
          user="postgres", password="admin")

DRY_RUN = "--dry-run" in sys.argv


def conn():
    c = psycopg2.connect(**DB)
    c.set_client_encoding("UTF8")
    return c


def col_list(connection, schema: str, table: str) -> list[str]:
    """Retourne la liste des colonnes d'une table."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
            ORDER BY ordinal_position
        """, (schema, table))
        return [r[0] for r in cur.fetchall()]


def table_exists(connection, schema: str, table: str) -> bool:
    with connection.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema=%s AND table_name=%s
        """, (schema, table))
        return cur.fetchone() is not None


def count_rows(connection, schema: str, table: str) -> int:
    with connection.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
        return cur.fetchone()[0]


def migrate_table(
    src_schema: str, src_table: str,
    dst_schema: str, dst_table: str,
    col_mapping: dict = None,   # {dst_col: src_col}  si noms différents
    where: str = None,          # filtre SQL optionnel
):
    """
    Migre les données d'une table vers une autre en mappant les colonnes communes.
    """
    c = conn()
    try:
        if not table_exists(c, src_schema, src_table):
            logger.info(f"  ⏭  {src_schema}.{src_table} n'existe pas — skip")
            return 0
        if not table_exists(c, dst_schema, dst_table):
            logger.warning(f"  ⚠  {dst_schema}.{dst_table} n'existe pas — skip")
            return 0

        src_cols = set(col_list(c, src_schema, src_table))
        dst_cols = set(col_list(c, dst_schema, dst_table))

        # Colonnes communes (exclure id/serial pour éviter conflits)
        common = sorted(
            (src_cols & dst_cols) - {"id"},
        )

        if not common:
            logger.warning(f"  ⚠  Pas de colonnes communes {src_table} → {dst_table}")
            return 0

        cols_str = ", ".join(common)
        src_where = f"WHERE {where}" if where else ""

        sql = f"""
            INSERT INTO {dst_schema}.{dst_table} ({cols_str})
            SELECT {cols_str} FROM {src_schema}.{src_table} {src_where}
            ON CONFLICT DO NOTHING
        """

        before = count_rows(c, dst_schema, dst_table)

        if DRY_RUN:
            logger.info(f"  [DRY] {src_schema}.{src_table} → {dst_schema}.{dst_table}")
            logger.info(f"        Colonnes: {common}")
            c.close()
            return 0

        with c.cursor() as cur:
            cur.execute(sql)
        c.commit()

        after = count_rows(c, dst_schema, dst_table)
        inserted = after - before
        total_src = count_rows(c, src_schema, src_table)
        logger.info(
            f"  ✓  {src_table} → {dst_schema}.{dst_table} | "
            f"+{inserted} lignes (src={total_src}, dst={after})"
        )
        return inserted

    except Exception as e:
        c.rollback()
        logger.error(f"  ✗  {src_table} → {dst_schema}.{dst_table}: {e}")
        return 0
    finally:
        c.close()


def add_tables_to_schemas():
    """Ajoute les tables manquantes dans les schémas cibles."""
    c = conn()
    try:
        with c.cursor() as cur:

            # ── SCHÉMA SALES — tables supplémentaires ─────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sales.transactions (
                    sale_id          VARCHAR(50) PRIMARY KEY,
                    date_vente       TIMESTAMP,
                    store_id         VARCHAR(10),
                    cod_prod         VARCHAR(30),
                    des_produit      VARCHAR(200),
                    agent_id         VARCHAR(20),
                    qte_produit      INT DEFAULT 1,
                    lig_ttc          NUMERIC(12,3),
                    lig_ht           NUMERIC(12,3),
                    lig_tva          NUMERIC(12,3),
                    tx_tva           NUMERIC(5,2),
                    forfait          VARCHAR(100),
                    type_abonnement  VARCHAR(50),
                    type_client      VARCHAR(20),
                    num_client       VARCHAR(30),
                    num_serie        VARCHAR(50),
                    date_only        DATE GENERATED ALWAYS AS (date_vente::DATE) STORED,
                    heure            SMALLINT,
                    jour_semaine     SMALLINT,
                    created_at       TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_tx_store
                    ON sales.transactions(store_id);
                CREATE INDEX IF NOT EXISTS idx_tx_date
                    ON sales.transactions(date_only);
                CREATE INDEX IF NOT EXISTS idx_tx_agent
                    ON sales.transactions(agent_id);
                CREATE INDEX IF NOT EXISTS idx_tx_store_date
                    ON sales.transactions(store_id, date_only);

                CREATE TABLE IF NOT EXISTS sales.transactions_history_raw (
                    sale_id           TEXT,
                    sale_ord          TEXT,
                    type_transaction  TEXT,
                    date_vente        TEXT,
                    code_centre       TEXT,
                    code_exercice     TEXT,
                    code_caisse       TEXT,
                    document          TEXT,
                    code_produit      TEXT,
                    des_produit       TEXT,
                    categorie_produit TEXT,
                    type_ligne        TEXT,
                    qte_produit       TEXT,
                    code_client       TEXT,
                    exonere           TEXT,
                    type_client       TEXT,
                    num_client        TEXT,
                    kit_code          TEXT,
                    num_serie         TEXT,
                    agent_id          TEXT,
                    agent_name        TEXT,
                    agent_surname     TEXT,
                    lig_ht            TEXT,
                    lig_tva           TEXT,
                    tx_tva            TEXT,
                    lig_net_ht        TEXT,
                    lig_ttc           TEXT,
                    situation         TEXT,
                    engagement        TEXT,
                    import_erp        TEXT,
                    import_dwh        TEXT,
                    date_insertion    TEXT,
                    attribute_2       TEXT,
                    attribute_3       TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_raw_centre
                    ON sales.transactions_history_raw(code_centre);

                CREATE TABLE IF NOT EXISTS sales.agents (
                    agent_id       VARCHAR(20) PRIMARY KEY,
                    agent_name     VARCHAR(50),
                    agent_surname  VARCHAR(50),
                    store_id       VARCHAR(10),
                    actif          BOOLEAN DEFAULT TRUE,
                    created_at     TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS sales.boutiques (
                    store_id       VARCHAR(10) PRIMARY KEY,
                    store_name     VARCHAR(150),
                    adresse        TEXT,
                    ville          VARCHAR(50),
                    type_boutique  VARCHAR(20),
                    statut         VARCHAR(10) DEFAULT 'A',
                    created_at     TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS sales.produits (
                    cod_prod    VARCHAR(30) PRIMARY KEY,
                    des_prod    VARCHAR(200),
                    cod_famille INT,
                    categorie   VARCHAR(50),
                    pv_ttc      NUMERIC(10,3),
                    pa_ttc      NUMERIC(10,3),
                    tx_marge    NUMERIC(6,2),
                    tx_tva      NUMERIC(5,2),
                    flag_5g     VARCHAR(1),
                    flag_4g     VARCHAR(1),
                    flag_3g     VARCHAR(1),
                    actif       VARCHAR(1) DEFAULT 'O',
                    created_at  TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS sales.objectifs (
                    id            SERIAL PRIMARY KEY,
                    store_id      VARCHAR(10),
                    agent_id      VARCHAR(20),
                    date_objectif DATE,
                    objectif_ca   NUMERIC(12,2),
                    created_at    TIMESTAMP DEFAULT NOW(),
                    UNIQUE(store_id, agent_id, date_objectif)
                );

                CREATE TABLE IF NOT EXISTS sales.patterns_horaires (
                    id               SERIAL PRIMARY KEY,
                    store_id         VARCHAR(10),
                    jour_semaine     SMALLINT,
                    heure            SMALLINT,
                    poids_moyen      NUMERIC(8,4),
                    ca_moyen         NUMERIC(12,2),
                    nb_observations  INT DEFAULT 0,
                    updated_at       TIMESTAMP DEFAULT NOW(),
                    UNIQUE(store_id, jour_semaine, heure)
                );

                CREATE TABLE IF NOT EXISTS sales.ratios_historiques (
                    id               SERIAL PRIMARY KEY,
                    store_id         VARCHAR(10),
                    jour_semaine     SMALLINT,
                    heure            SMALLINT,
                    ratio_eod        NUMERIC(8,4),
                    ca_heure_moyen   NUMERIC(12,2),
                    ca_eod_moyen     NUMERIC(12,2),
                    nb_observations  INT DEFAULT 0,
                    updated_at       TIMESTAMP DEFAULT NOW(),
                    UNIQUE(store_id, jour_semaine, heure)
                );

                CREATE TABLE IF NOT EXISTS sales.coaching_cards (
                    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                    store_id       VARCHAR(10),
                    agent_id       VARCHAR(20),
                    cycle_id       VARCHAR(50),
                    date_conseil   TIMESTAMP DEFAULT NOW(),
                    urgence        VARCHAR(10),
                    gap_pct        NUMERIC(6,2),
                    gap_amount     NUMERIC(12,2),
                    forecast_eod   NUMERIC(12,2),
                    analyst_summary TEXT,
                    strategie      TEXT,
                    actions        JSONB,
                    cause_racine   TEXT,
                    contexte       JSONB,
                    statut         VARCHAR(20) DEFAULT 'pending',
                    created_at     TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cards_store
                    ON sales.coaching_cards(store_id);
                CREATE INDEX IF NOT EXISTS idx_cards_created
                    ON sales.coaching_cards(created_at DESC);
            """)

            # ── SCHÉMA MONITORING — tables supplémentaires ─────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS monitoring.agent_memory (
                    id           SERIAL PRIMARY KEY,
                    agent_name   VARCHAR(30),
                    store_id     VARCHAR(10),
                    cycle_id     VARCHAR(50),
                    memory_type  VARCHAR(30),
                    memory_data  JSONB,
                    created_at   TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS monitoring.strategy_memory (
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
            """)

        c.commit()
        logger.info("[SETUP] Tables supplémentaires créées ✅")
    except Exception as e:
        c.rollback()
        logger.error(f"[SETUP] Erreur création tables: {e}")
    finally:
        c.close()


def run_migration():
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  MIGRATION public → sales | inventory | monitoring       ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    if DRY_RUN:
        logger.info("MODE DRY-RUN — aucune donnée ne sera modifiée\n")

    # 1. Créer les tables manquantes
    logger.info("\n=== 1. Tables supplémentaires ===")
    add_tables_to_schemas()

    # ── SCHÉMA SALES ────────────────────────────────────────────────────────
    logger.info("\n=== 2. Migration → sales ===")

    migrate_table("public", "app_users",     "sales", "app_users")
    migrate_table("public", "app_sessions",  "sales", "app_sessions")
    migrate_table("public", "coaching_scripts", "sales", "coaching_scripts")
    migrate_table("public", "coach_interactions", "sales", "coach_interactions")
    migrate_table("public", "coaching_cards", "sales", "coaching_cards")
    migrate_table("public", "agents",        "sales", "agents")
    migrate_table("public", "boutiques",     "sales", "boutiques")
    migrate_table("public", "produits",      "sales", "produits")
    migrate_table("public", "objectifs",     "sales", "objectifs")
    migrate_table("public", "patterns_horaires",  "sales", "patterns_horaires")
    migrate_table("public", "ratios_historiques",  "sales", "ratios_historiques")

    # Transactions (table critique pour le coaching)
    migrate_table("public", "transactions",  "sales", "transactions")

    # Transactions raw (données brutes CSV importées)
    migrate_table(
        "public", "transactions_history_raw",
        "sales",  "transactions_history_raw"
    )

    # ── SCHÉMA MONITORING ────────────────────────────────────────────────────
    logger.info("\n=== 3. Migration → monitoring ===")

    migrate_table("public", "agent_logs",        "monitoring", "agent_logs")
    migrate_table("public", "agent_cycles",      "monitoring", "agent_cycles")
    migrate_table("public", "agent_errors",      "monitoring", "agent_errors")
    migrate_table("public", "rag_feedback",      "monitoring", "rag_feedback")
    migrate_table("public", "agent_memory",      "monitoring", "agent_memory")
    migrate_table("public", "strategy_memory",   "monitoring", "strategy_memory")

    # ── SCHÉMA INVENTORY ────────────────────────────────────────────────────
    logger.info("\n=== 4. Migration → inventory ===")

    migrate_table("public", "stock", "inventory", "stock_levels",
                  # Mapping colonnes si noms différents
                  )

    # ── VÉRIFICATION FINALE ─────────────────────────────────────────────────
    logger.info("\n=== 5. Vérification finale ===")

    c = conn()
    with c.cursor() as cur:
        checks = [
            ("sales",      "transactions"),
            ("sales",      "transactions_history_raw"),
            ("sales",      "coaching_scripts"),
            ("sales",      "coach_interactions"),
            ("sales",      "app_users"),
            ("sales",      "agents"),
            ("sales",      "boutiques"),
            ("sales",      "produits"),
            ("sales",      "objectifs"),
            ("sales",      "patterns_horaires"),
            ("sales",      "ratios_historiques"),
            ("sales",      "coaching_cards"),
            ("inventory",  "stock_levels"),
            ("monitoring", "agent_logs"),
            ("monitoring", "agent_cycles"),
            ("monitoring", "agent_errors"),
            ("monitoring", "rag_feedback"),
            ("monitoring", "strategy_memory"),
        ]
        for schema, table in checks:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
                n = cur.fetchone()[0]
                status = "✓" if n > 0 else "○"
                logger.info(f"  {status} {schema}.{table}: {n:,} lignes")
            except Exception as e:
                logger.warning(f"  ✗ {schema}.{table}: {e}")
    c.close()

    logger.info("\n✅ Migration terminée !")
    if not DRY_RUN:
        logger.info("\nProchaine étape :")
        logger.info("  python setup_db.py  (re-run pour s'assurer que tout est OK)")
        logger.info("  uvicorn main:app --port 8000")


if __name__ == "__main__":
    run_migration()