"""
fix_migration.py — Corrige les 5 migrations échouées :
  1. app_users      : user_id VARCHAR → INTEGER cast
  2. app_sessions   : user_id VARCHAR → INTEGER cast
  3. produits       : actif VARCHAR(1) trop petit → VARCHAR(5)
  4. ratios_historiques : ratio_eod dépassant NUMERIC(8,4)
  5. transactions   : colonne date_only générée → exclure
  6. stock          : cod_prod → sku, migration complète
"""

import psycopg2
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DB = dict(host="localhost", port=5432, dbname="ooredoo_sales",
          user="postgres", password="admin")


def get_conn():
    c = psycopg2.connect(**DB)
    c.set_client_encoding("UTF8")
    return c


def run(label, sql, conn):
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        logger.info(f"  ✓  {label}")
    except Exception as e:
        conn.rollback()
        logger.error(f"  ✗  {label}: {e}")


# ══════════════════════════════════════════════════════════════
# 1. app_users — user_id est VARCHAR dans public, INT dans sales
# ══════════════════════════════════════════════════════════════
def fix_app_users():
    conn = get_conn()
    run("app_users — drop old", """
        TRUNCATE sales.app_users RESTART IDENTITY CASCADE
    """, conn)
    run("app_users — recreate with correct types", """
        ALTER TABLE sales.app_users
            ALTER COLUMN user_id TYPE VARCHAR(50)
    """, conn)
    run("app_users — migrate", """
        INSERT INTO sales.app_users
            (user_id, username, password_hash, full_name, role,
             store_id, store_name, initials, color, advisor_id,
             actif, last_login, created_at)
        SELECT
            user_id::VARCHAR, username, password_hash, full_name, role,
            store_id, store_name, initials, color, advisor_id,
            actif, last_login, created_at
        FROM public.app_users
        ON CONFLICT (username) DO NOTHING
    """, conn)

    # Compter
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales.app_users")
        n = cur.fetchone()[0]
    logger.info(f"     → sales.app_users: {n} lignes")
    conn.close()


# ══════════════════════════════════════════════════════════════
# 2. app_sessions — user_id type mismatch
# ══════════════════════════════════════════════════════════════
def fix_app_sessions():
    conn = get_conn()
    run("app_sessions — alter user_id type", """
        ALTER TABLE sales.app_sessions
            ALTER COLUMN user_id TYPE VARCHAR(50)
    """, conn)
    run("app_sessions — migrate", """
        INSERT INTO sales.app_sessions
            (token, user_id, expires_at, last_used, ip_address, created_at)
        SELECT
            token, user_id::VARCHAR, expires_at, last_used, ip_address, created_at
        FROM public.app_sessions
        ON CONFLICT DO NOTHING
    """, conn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales.app_sessions")
        n = cur.fetchone()[0]
    logger.info(f"     → sales.app_sessions: {n} lignes")
    conn.close()


# ══════════════════════════════════════════════════════════════
# 3. produits — actif VARCHAR(1) trop petit (valeur 'O' ou 'N')
# ══════════════════════════════════════════════════════════════
def fix_produits():
    conn = get_conn()
    # Recréer la table avec actif VARCHAR(5)
    run("produits — drop and recreate", """
        DROP TABLE IF EXISTS sales.produits CASCADE
    """, conn)
    run("produits — create", """
        CREATE TABLE sales.produits (
            cod_prod    VARCHAR(30) PRIMARY KEY,
            des_prod    VARCHAR(200),
            cod_famille INT,
            categorie   VARCHAR(50),
            pv_ttc      NUMERIC(12,3),
            pa_ttc      NUMERIC(12,3),
            tx_marge    NUMERIC(6,2),
            tx_tva      NUMERIC(5,2),
            flag_5g     VARCHAR(5),
            flag_4g     VARCHAR(5),
            flag_3g     VARCHAR(5),
            actif       VARCHAR(5) DEFAULT 'O',
            created_at  TIMESTAMP DEFAULT NOW()
        )
    """, conn)
    run("produits — migrate", """
        INSERT INTO sales.produits
            (cod_prod, des_prod, cod_famille, categorie,
             pv_ttc, pa_ttc, tx_marge, tx_tva,
             flag_5g, flag_4g, flag_3g, actif, created_at)
        SELECT
            cod_prod, des_prod, cod_famille, categorie,
            pv_ttc, pa_ttc, tx_marge, tx_tva,
            flag_5g, flag_4g, flag_3g, actif, created_at
        FROM public.produits
        ON CONFLICT DO NOTHING
    """, conn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales.produits")
        n = cur.fetchone()[0]
    logger.info(f"     → sales.produits: {n} lignes")
    conn.close()


# ══════════════════════════════════════════════════════════════
# 4. ratios_historiques — ratio_eod dépasse NUMERIC(8,4) → max=53595
# ══════════════════════════════════════════════════════════════
def fix_ratios():
    conn = get_conn()
    run("ratios — drop and recreate", """
        DROP TABLE IF EXISTS sales.ratios_historiques CASCADE
    """, conn)
    run("ratios — create with wider precision", """
        CREATE TABLE sales.ratios_historiques (
            id               SERIAL PRIMARY KEY,
            store_id         VARCHAR(10),
            jour_semaine     SMALLINT,
            heure            SMALLINT,
            ratio_eod        NUMERIC(12,4),
            ca_heure_moyen   NUMERIC(12,2),
            ca_eod_moyen     NUMERIC(12,2),
            nb_observations  INT DEFAULT 0,
            updated_at       TIMESTAMP DEFAULT NOW(),
            UNIQUE(store_id, jour_semaine, heure)
        )
    """, conn)
    run("ratios — migrate", """
        INSERT INTO sales.ratios_historiques
            (store_id, jour_semaine, heure, ratio_eod,
             ca_heure_moyen, ca_eod_moyen, nb_observations, updated_at)
        SELECT
            store_id, jour_semaine, heure, ratio_eod,
            ca_heure_moyen, ca_eod_moyen, nb_observations, updated_at
        FROM public.ratios_historiques
        ON CONFLICT DO NOTHING
    """, conn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales.ratios_historiques")
        n = cur.fetchone()[0]
    logger.info(f"     → sales.ratios_historiques: {n} lignes")
    conn.close()


# ══════════════════════════════════════════════════════════════
# 5. transactions — exclure date_only (colonne générée)
# ══════════════════════════════════════════════════════════════
def fix_transactions():
    conn = get_conn()
    run("transactions — migrate (sans date_only)", """
        INSERT INTO sales.transactions
            (sale_id, date_vente, store_id, cod_prod, des_produit,
             agent_id, qte_produit, lig_ttc, lig_ht, lig_tva, tx_tva,
             forfait, type_abonnement, type_client, num_client,
             num_serie, heure, jour_semaine, created_at)
        SELECT
            sale_id, date_vente, store_id, cod_prod, des_produit,
            agent_id, qte_produit, lig_ttc, lig_ht, lig_tva, tx_tva,
            forfait, type_abonnement, type_client, num_client,
            num_serie, heure, jour_semaine, created_at
        FROM public.transactions
        ON CONFLICT (sale_id) DO NOTHING
    """, conn)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales.transactions")
        n = cur.fetchone()[0]
    logger.info(f"     → sales.transactions: {n} lignes")
    conn.close()


# ══════════════════════════════════════════════════════════════
# 6. stock → inventory.stock_levels (mapping colonnes)
# ══════════════════════════════════════════════════════════════
def fix_stock():
    conn = get_conn()
    # Recréer stock_levels avec les colonnes du stock public
    run("stock_levels — drop and recreate", """
        DROP TABLE IF EXISTS inventory.stock_levels CASCADE
    """, conn)
    run("stock_levels — create aligned with public.stock", """
        CREATE TABLE inventory.stock_levels (
            id           SERIAL PRIMARY KEY,
            store_id     VARCHAR(10) NOT NULL,
            sku          VARCHAR(50) NOT NULL,
            product_name VARCHAR(200),
            category     VARCHAR(50),
            quantity     INT DEFAULT 0,
            qty_sold     INT DEFAULT 0,
            qty_reserved INT DEFAULT 0,
            min_stock    INT DEFAULT 5,
            max_stock    INT DEFAULT 100,
            reorder_point INT DEFAULT 10,
            unit_cost    NUMERIC(12,3) DEFAULT 0,
            risk_level   VARCHAR(20) DEFAULT 'low',
            days_of_stock FLOAT DEFAULT 0,
            last_updated TIMESTAMP DEFAULT NOW(),
            UNIQUE(store_id, sku)
        )
    """, conn)
    run("stock_levels — create indexes", """
        CREATE INDEX IF NOT EXISTS idx_stock_store
            ON inventory.stock_levels(store_id);
        CREATE INDEX IF NOT EXISTS idx_stock_risk
            ON inventory.stock_levels(risk_level);
        CREATE INDEX IF NOT EXISTS idx_stock_store_sku
            ON inventory.stock_levels(store_id, sku)
    """, conn)
    # Migrer depuis public.stock (cod_prod → sku)
    run("stock_levels — migrate from public.stock", """
        INSERT INTO inventory.stock_levels
            (store_id, sku, quantity, qty_sold, qty_reserved, last_updated)
        SELECT
            store_id,
            cod_prod,       -- cod_prod → sku
            COALESCE(qte_stk, 0),
            COALESCE(qte_vte, 0),
            COALESCE(qte_res, 0),
            COALESCE(dat_maj, NOW())
        FROM public.stock
        WHERE cod_prod IS NOT NULL
          AND store_id IS NOT NULL
        ON CONFLICT (store_id, sku) DO UPDATE SET
            quantity     = EXCLUDED.quantity,
            qty_sold     = EXCLUDED.qty_sold,
            qty_reserved = EXCLUDED.qty_reserved,
            last_updated = EXCLUDED.last_updated
    """, conn)

    # Joindre avec produits pour enrichir les noms
    run("stock_levels — enrich with product names", """
        UPDATE inventory.stock_levels sl
        SET product_name = p.des_prod,
            unit_cost    = COALESCE(p.pa_ttc, 0)
        FROM public.produits p
        WHERE sl.sku = p.cod_prod
          AND sl.product_name IS NULL
    """, conn)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM inventory.stock_levels")
        n = cur.fetchone()[0]
        cur.execute("""
            SELECT store_id, COUNT(*) as nb_sku, SUM(quantity) as total_qty
            FROM inventory.stock_levels
            GROUP BY store_id
            ORDER BY nb_sku DESC LIMIT 5
        """)
        top = cur.fetchall()

    logger.info(f"     → inventory.stock_levels: {n} lignes")
    logger.info(f"     Top 5 boutiques par SKU:")
    for store, nb, qty in top:
        logger.info(f"       {store}: {nb} SKUs, {qty} unités total")
    conn.close()


# ══════════════════════════════════════════════════════════════
# VÉRIFICATION FINALE
# ══════════════════════════════════════════════════════════════
def verify():
    conn = get_conn()
    logger.info("\n=== VÉRIFICATION FINALE ===")
    checks = [
        ("sales",      "app_users"),
        ("sales",      "app_sessions"),
        ("sales",      "produits"),
        ("sales",      "ratios_historiques"),
        ("sales",      "transactions"),
        ("sales",      "transactions_history_raw"),
        ("sales",      "coaching_scripts"),
        ("sales",      "agents"),
        ("sales",      "boutiques"),
        ("sales",      "objectifs"),
        ("sales",      "patterns_horaires"),
        ("sales",      "coaching_cards"),
        ("inventory",  "stock_levels"),
        ("monitoring", "agent_logs"),
        ("monitoring", "agent_cycles"),
        ("monitoring", "agent_errors"),
        ("monitoring", "rag_feedback"),
    ]
    total_ok = 0
    with conn.cursor() as cur:
        for schema, table in checks:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
                n = cur.fetchone()[0]
                status = "✓" if n > 0 else "○"
                if n > 0:
                    total_ok += 1
                logger.info(f"  {status} {schema}.{table}: {n:,}")
            except Exception as e:
                logger.error(f"  ✗ {schema}.{table}: {e}")
    conn.close()
    logger.info(f"\n{total_ok}/{len(checks)} tables avec données ✅")


if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║  FIX MIGRATION — Tables échouées             ║")
    logger.info("╚══════════════════════════════════════════════╝")

    logger.info("\n=== 1. app_users ===")
    fix_app_users()

    logger.info("\n=== 2. app_sessions ===")
    fix_app_sessions()

    logger.info("\n=== 3. produits ===")
    fix_produits()

    logger.info("\n=== 4. ratios_historiques ===")
    fix_ratios()

    logger.info("\n=== 5. transactions ===")
    fix_transactions()

    logger.info("\n=== 6. stock → inventory.stock_levels ===")
    fix_stock()

    verify()

    logger.info("\n✅ Fix terminé — relancez uvicorn main:app --port 8000")