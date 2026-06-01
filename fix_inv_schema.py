"""
fix_inv_schema.py — Remplace tous les inv. par inventory. dans inventory_repo.py
et crée les tables manquantes dans le schéma inventory.
"""
import os
import psycopg2

BASE = r"C:\Users\malek\Desktop\PFE-Backend"

# ── 1. Remplacer inv. par inventory. dans inventory_repo.py ──────────────────
repo_path = os.path.join(BASE, r"inventory-module\db\repositories\inventory_repo.py")

with open(repo_path, "r", encoding="utf-8") as f:
    content = f.read()

replacements = [
    ("inv.stock_levels",       "inventory.stock_levels"),
    ("inv.stock_movements",    "inventory.stock_movements"),
    ("inv.stock_alerts",       "inventory.stock_alerts"),
    ("inv.products",           "inventory.products"),
    ("inv.alerts",             "inventory.alerts"),
    ("inv.stores",             "inventory.stores"),
    ("inv.business_objectives","inventory.business_objectives"),
    ("inv.agent_runs",         "inventory.agent_runs"),
    ("inv.reorder_queue",      "inventory.reorder_queue"),
    # search_path
    ("search_path TO inv",     "search_path TO inventory"),
    ('"inv"',                  '"inventory"'),
    ("schema='inv'",           "schema='inventory'"),
    ("schema = 'inv'",         "schema = 'inventory'"),
]

count = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        count += 1
        print(f"  ✓ {old} → {new}")

with open(repo_path, "w", encoding="utf-8") as f:
    f.write(content)
print(f"\ninventory_repo.py: {count} remplacements\n")

# ── 2. Créer les tables manquantes dans schéma inventory ─────────────────────
conn = psycopg2.connect(
    host="localhost", port=5432, dbname="ooredoo_sales",
    user="postgres", password="admin"
)
conn.set_client_encoding("UTF8")
cur = conn.cursor()

tables_sql = """
    -- Products (catalogue produits inventory)
    CREATE TABLE IF NOT EXISTS inventory.products (
        sku           VARCHAR(50) PRIMARY KEY,
        product_name  VARCHAR(200),
        category      VARCHAR(50),
        subcategory   VARCHAR(50),
        unit_cost     NUMERIC(12,3) DEFAULT 0,
        unit_price    NUMERIC(12,3) DEFAULT 0,
        lead_time_days INT DEFAULT 7,
        moq           INT DEFAULT 1,
        lifecycle_stage VARCHAR(20) DEFAULT 'mature',
        active        BOOLEAN DEFAULT TRUE,
        created_at    TIMESTAMP DEFAULT NOW(),
        updated_at    TIMESTAMP DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_products_category
        ON inventory.products(category);
    CREATE INDEX IF NOT EXISTS idx_products_active
        ON inventory.products(active);

    -- Alerts stock
    CREATE TABLE IF NOT EXISTS inventory.alerts (
        id            SERIAL PRIMARY KEY,
        store_id      VARCHAR(10),
        sku           VARCHAR(50),
        alert_type    VARCHAR(30),
        severity      VARCHAR(10) DEFAULT 'medium',
        message       TEXT,
        resolved      BOOLEAN DEFAULT FALSE,
        resolved_at   TIMESTAMP,
        created_at    TIMESTAMP DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_alerts_store
        ON inventory.alerts(store_id);
    CREATE INDEX IF NOT EXISTS idx_alerts_resolved
        ON inventory.alerts(resolved);

    -- Stores (boutiques dans inventory)
    CREATE TABLE IF NOT EXISTS inventory.stores (
        store_id      VARCHAR(10) PRIMARY KEY,
        store_name    VARCHAR(150),
        region        VARCHAR(50),
        active        BOOLEAN DEFAULT TRUE,
        created_at    TIMESTAMP DEFAULT NOW()
    );

    -- Business objectives
    CREATE TABLE IF NOT EXISTS inventory.business_objectives (
        id            SERIAL PRIMARY KEY,
        label         VARCHAR(50) UNIQUE NOT NULL,
        description   TEXT,
        priority      INT DEFAULT 1,
        is_active     BOOLEAN DEFAULT FALSE,
        created_at    TIMESTAMP DEFAULT NOW()
    );

    -- Agent runs (monitoring inventory)
    CREATE TABLE IF NOT EXISTS inventory.agent_runs (
        id            SERIAL PRIMARY KEY,
        agent_name    VARCHAR(50),
        store_id      VARCHAR(10),
        status        VARCHAR(20) DEFAULT 'running',
        started_at    TIMESTAMP DEFAULT NOW(),
        completed_at  TIMESTAMP,
        nb_skus       INT DEFAULT 0,
        nb_alerts     INT DEFAULT 0,
        error_msg     TEXT
    );

    -- Reorder queue
    CREATE TABLE IF NOT EXISTS inventory.reorder_queue (
        id            SERIAL PRIMARY KEY,
        store_id      VARCHAR(10),
        sku           VARCHAR(50),
        qty_ordered   INT,
        status        VARCHAR(20) DEFAULT 'pending',
        approved_at   TIMESTAMP,
        arrival_date  DATE,
        created_at    TIMESTAMP DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_reorder_store
        ON inventory.reorder_queue(store_id, status);
"""

try:
    cur.execute(tables_sql)
    conn.commit()
    print("✅ Tables inventory créées")
except Exception as e:
    conn.rollback()
    print(f"✗ Erreur création tables: {e}")

# ── 3. Peupler inventory.products depuis public.produits ─────────────────────
try:
    cur.execute("""
        INSERT INTO inventory.products
            (sku, product_name, category, unit_cost, active)
        SELECT
            cod_prod,
            des_prod,
            CASE
                WHEN cod_famille IN (10,15,52) THEN 'Forfait'
                WHEN cod_famille IN (30)        THEN 'Terminal'
                WHEN cod_famille IN (40,41,44)  THEN 'Accessoire'
                WHEN cod_famille IN (11,12,13)  THEN 'Recharge'
                WHEN cod_famille IN (20,21)     THEN 'SIM'
                WHEN cod_famille IN (60)        THEN 'Box Fibre'
                WHEN cod_famille IN (70)        THEN 'Service'
                ELSE 'Autre'
            END,
            COALESCE(pa_ttc, 0),
            actif = 'O'
        FROM public.produits
        ON CONFLICT (sku) DO NOTHING
    """)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM inventory.products")
    print(f"✅ inventory.products: {cur.fetchone()[0]} produits")
except Exception as e:
    conn.rollback()
    print(f"✗ Erreur products: {e}")

# ── 4. Peupler inventory.stores depuis public.boutiques ──────────────────────
try:
    cur.execute("""
        INSERT INTO inventory.stores (store_id, store_name, active)
        SELECT store_id, store_name, statut = 'A'
        FROM public.boutiques
        ON CONFLICT (store_id) DO NOTHING
    """)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM inventory.stores")
    print(f"✅ inventory.stores: {cur.fetchone()[0]} boutiques")
except Exception as e:
    conn.rollback()
    print(f"✗ Erreur stores: {e}")

# ── 5. Insérer les objectifs business par défaut ─────────────────────────────
try:
    objectives = [
        ("balanced",      "Standard safety level",          1, True),
        ("cost",          "Minimize spending",               2, False),
        ("service_level", "Maximize availability",           3, False),
        ("competitive",   "Proactive stocking",              4, False),
    ]
    for label, desc, priority, is_active in objectives:
        cur.execute("""
            INSERT INTO inventory.business_objectives
                (label, description, priority, is_active)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (label) DO NOTHING
        """, (label, desc, priority, is_active))
    conn.commit()
    print("✅ inventory.business_objectives: 4 objectifs")
except Exception as e:
    conn.rollback()
    print(f"✗ Erreur objectives: {e}")

# ── 6. Vérification finale ────────────────────────────────────────────────────
print("\n=== Vérification schéma inventory ===")
cur.execute("""
    SELECT tablename FROM pg_tables
    WHERE schemaname='inventory' ORDER BY tablename
""")
tables = [r[0] for r in cur.fetchall()]
print(f"Tables: {tables}")

for t in tables:
    cur.execute(f"SELECT COUNT(*) FROM inventory.{t}")
    n = cur.fetchone()[0]
    status = "✓" if n > 0 else "○"
    print(f"  {status} inventory.{t}: {n:,}")

conn.close()
print("\n✅ Fix terminé — relancez: uvicorn main:app --port 8000")