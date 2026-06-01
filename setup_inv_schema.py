"""
setup_inv_schema.py — Crée le schéma inv complet (migrations 001 + 002)
et le peuple depuis :
  - product_master.csv    → inv.products
  - stock_history.csv     → inv.stock_levels
  - sales_history.csv     → inv.stores
  - promotions.csv        → inv.promotions
  - public.produits       → inv.products (complément)
  - public.boutiques      → inv.stores (complément)
  - public.stock          → inv.stock_levels (complément)

Usage :
    python setup_inv_schema.py
"""

import os
import sys
import logging
import psycopg2
import psycopg2.extras
import pandas as pd
from pathlib import Path
from datetime import datetime, date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE      = Path(r"C:\Users\malek\Desktop\PFE-Backend")
INV_DATA  = BASE / "inventory-module" / "data" / "processed"
FORECAST  = BASE / "inventory-module" / "data" / "forecasts"

DB = dict(host="localhost", port=5432, dbname="ooredoo_sales",
          user="postgres", password="admin")


def conn():
    c = psycopg2.connect(**DB)
    c.set_client_encoding("UTF8")
    return c


def run(label: str, sql: str, c=None):
    close = False
    if c is None:
        c = conn()
        close = True
    try:
        with c.cursor() as cur:
            cur.execute(sql)
        c.commit()
        logger.info(f"  ✓  {label}")
    except Exception as e:
        c.rollback()
        logger.error(f"  ✗  {label}: {str(e)[:120]}")
    finally:
        if close:
            c.close()


# ══════════════════════════════════════════════════════════════
# 1. Créer le schéma inv + toutes les tables (migrations 001+002)
# ══════════════════════════════════════════════════════════════
def create_schema():
    logger.info("=== 1. Création schéma inv ===")
    c = conn()

    run("CREATE SCHEMA inv", "CREATE SCHEMA IF NOT EXISTS inv", c)
    run("CREATE EXTENSION uuid-ossp", 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"', c)

    tables = [
        ("inv.stores", """
            CREATE TABLE IF NOT EXISTS inv.stores (
                store_id    VARCHAR(50)  PRIMARY KEY,
                store_name  VARCHAR(200) NOT NULL DEFAULT '',
                region      VARCHAR(100),
                active      BOOLEAN      DEFAULT TRUE,
                created_at  TIMESTAMP    DEFAULT NOW(),
                updated_at  TIMESTAMP    DEFAULT NOW()
            )
        """),
        ("inv.products", """
            CREATE TABLE IF NOT EXISTS inv.products (
                sku               VARCHAR(50)    PRIMARY KEY,
                product_name      VARCHAR(200)   NOT NULL DEFAULT '',
                category          VARCHAR(100),
                unit_cost         NUMERIC(12, 4),
                unit_price        NUMERIC(12, 4),
                lead_time_days    INTEGER        DEFAULT 7,
                lead_time_std     NUMERIC(8, 4)  DEFAULT 1.0,
                moq               INTEGER        DEFAULT 1,
                holding_cost_pct  NUMERIC(8, 6)  DEFAULT 0.02,
                order_cost        NUMERIC(12, 4) DEFAULT 50.0,
                lifecycle_stage   VARCHAR(20)    DEFAULT 'mature'
                    CHECK (lifecycle_stage IN ('growth','mature','decline','discontinued')),
                created_at        TIMESTAMP      DEFAULT NOW(),
                updated_at        TIMESTAMP      DEFAULT NOW()
            )
        """),
        ("inv.stock_levels", """
            CREATE TABLE IF NOT EXISTS inv.stock_levels (
                sku                     VARCHAR(50)   NOT NULL
                    REFERENCES inv.products(sku) ON DELETE CASCADE,
                store_id                VARCHAR(50)   NOT NULL
                    REFERENCES inv.stores(store_id) ON DELETE CASCADE,
                stock_current           INTEGER       NOT NULL DEFAULT 0,
                stock_in_transit        INTEGER       DEFAULT 0,
                stock_min               INTEGER       DEFAULT 5,
                stock_max               INTEGER       DEFAULT 100,
                remaining_days_of_stock NUMERIC(8, 2) DEFAULT 0,
                last_received_at        TIMESTAMP,
                last_updated            TIMESTAMP     DEFAULT NOW(),
                PRIMARY KEY (sku, store_id)
            )
        """),
        ("idx stock_levels store", "CREATE INDEX IF NOT EXISTS idx_stock_levels_store ON inv.stock_levels(store_id)"),
        ("inv.demand_forecast", """
            CREATE TABLE IF NOT EXISTS inv.demand_forecast (
                id              SERIAL        PRIMARY KEY,
                sku             VARCHAR(50)   NOT NULL REFERENCES inv.products(sku),
                store_id        VARCHAR(50)   NOT NULL REFERENCES inv.stores(store_id),
                forecast_date   DATE          NOT NULL,
                demand_24h      NUMERIC(12, 4) NOT NULL DEFAULT 0,
                confidence_low  NUMERIC(12, 4),
                confidence_high NUMERIC(12, 4),
                model_version   VARCHAR(50),
                created_at      TIMESTAMP     DEFAULT NOW(),
                UNIQUE (sku, store_id, forecast_date)
            )
        """),
        ("idx demand_forecast date", "CREATE INDEX IF NOT EXISTS idx_forecast_date ON inv.demand_forecast(forecast_date)"),
        ("inv.agent_runs", """
            CREATE TABLE IF NOT EXISTS inv.agent_runs (
                id                        UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
                agent_name                VARCHAR(50)  NOT NULL DEFAULT 'analysis_agent',
                store_id                  VARCHAR(50)  REFERENCES inv.stores(store_id),
                started_at                TIMESTAMP    DEFAULT NOW(),
                completed_at              TIMESTAMP,
                status                    VARCHAR(20)  DEFAULT 'running'
                    CHECK (status IN ('running','completed','failed')),
                error_message             TEXT,
                items_processed           INTEGER      DEFAULT 0,
                alerts_generated          INTEGER      DEFAULT 0,
                recommendations_generated INTEGER      DEFAULT 0,
                created_at                TIMESTAMP    DEFAULT NOW()
            )
        """),
        ("inv.alerts", """
            CREATE TABLE IF NOT EXISTS inv.alerts (
                id                      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
                sku                     VARCHAR(50)  NOT NULL REFERENCES inv.products(sku),
                store_id                VARCHAR(50)  NOT NULL REFERENCES inv.stores(store_id),
                alert_type              VARCHAR(30)  NOT NULL DEFAULT 'stockout_risk'
                    CHECK (alert_type IN ('stockout_critical','stockout_risk','overstock','slow_moving')),
                severity                VARCHAR(10)  NOT NULL DEFAULT 'medium'
                    CHECK (severity IN ('critical','high','medium','low')),
                recommended_action      TEXT,
                status                  VARCHAR(20)  DEFAULT 'pending'
                    CHECK (status IN ('pending','validated','dismissed','resolved')),
                triggered_at            TIMESTAMP    DEFAULT NOW(),
                resolved_at             TIMESTAMP,
                estimated_stockout_date DATE,
                actual_stockout_date    DATE,
                was_accurate            BOOLEAN,
                created_at              TIMESTAMP    DEFAULT NOW()
            )
        """),
        ("inv.recommendations", """
            CREATE TABLE IF NOT EXISTS inv.recommendations (
                id                  UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
                sku                 VARCHAR(50)  NOT NULL REFERENCES inv.products(sku),
                store_id            VARCHAR(50)  NOT NULL REFERENCES inv.stores(store_id),
                agent_run_id        UUID         REFERENCES inv.agent_runs(id),
                recommendation_type VARCHAR(20)  NOT NULL DEFAULT 'reorder'
                    CHECK (recommendation_type IN ('reorder','transfer','promotion','markdown')),
                recommendation_text TEXT,
                suggested_quantity  INTEGER,
                confidence          NUMERIC(5, 4),
                status              VARCHAR(20)  DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected')),
                decided_by          VARCHAR(100),
                decided_at          TIMESTAMP,
                created_at          TIMESTAMP    DEFAULT NOW()
            )
        """),
        ("inv.promotions", """
            CREATE TABLE IF NOT EXISTS inv.promotions (
                promo_id     VARCHAR(50)   PRIMARY KEY,
                promo_name   VARCHAR(200)  NOT NULL DEFAULT '',
                promo_type   VARCHAR(100),
                start_date   DATE          NOT NULL DEFAULT CURRENT_DATE,
                end_date     DATE          NOT NULL DEFAULT CURRENT_DATE,
                sku          VARCHAR(50),
                category     VARCHAR(100),
                discount_pct NUMERIC(6, 2),
                scope        VARCHAR(20)   DEFAULT 'national'
                    CHECK (scope IN ('national','regional','store')),
                is_active    BOOLEAN       DEFAULT FALSE,
                created_at   TIMESTAMP     DEFAULT NOW()
            )
        """),
        ("idx promotions dates", "CREATE INDEX IF NOT EXISTS idx_promo_dates ON inv.promotions(start_date, end_date)"),
        ("inv.events", """
            CREATE TABLE IF NOT EXISTS inv.events (
                event_id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
                event_name           VARCHAR(200) NOT NULL DEFAULT '',
                event_type           VARCHAR(100),
                start_date           DATE         NOT NULL DEFAULT CURRENT_DATE,
                end_date             DATE         NOT NULL DEFAULT CURRENT_DATE,
                affected_categories  JSONB,
                estimated_uplift_pct NUMERIC(6, 2),
                scope                VARCHAR(20)  DEFAULT 'national'
                    CHECK (scope IN ('national','regional','store')),
                created_at           TIMESTAMP    DEFAULT NOW()
            )
        """),
        ("idx events dates", "CREATE INDEX IF NOT EXISTS idx_events_dates ON inv.events(start_date, end_date)"),
        ("inv.business_objectives", """
            CREATE TABLE IF NOT EXISTS inv.business_objectives (
                id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
                objective_type VARCHAR(30)  NOT NULL DEFAULT 'maximize_service_level'
                    CHECK (objective_type IN (
                        'minimize_cost','maximize_service_level',
                        'clear_stock','prioritize_margin'
                    )),
                priority       INTEGER      DEFAULT 1,
                target_value   NUMERIC(12, 4),
                applies_to     VARCHAR(10)  DEFAULT 'all'
                    CHECK (applies_to IN ('all','category','sku')),
                category       VARCHAR(100),
                sku            VARCHAR(50),
                start_date     DATE,
                end_date       DATE,
                is_active      BOOLEAN      DEFAULT TRUE,
                created_at     TIMESTAMP    DEFAULT NOW()
            )
        """),
        ("inv.context_adjustments", """
            CREATE TABLE IF NOT EXISTS inv.context_adjustments (
                id               UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
                sku              VARCHAR(50)  NOT NULL REFERENCES inv.products(sku),
                store_id         VARCHAR(50)  NOT NULL REFERENCES inv.stores(store_id),
                valid_from       DATE         NOT NULL DEFAULT CURRENT_DATE,
                valid_to         DATE         NOT NULL DEFAULT CURRENT_DATE,
                demand_uplift_pct NUMERIC(6,2),
                dominant_signal  VARCHAR(100),
                confidence       NUMERIC(5,4),
                interpretation   TEXT,
                agent_run_id     UUID         REFERENCES inv.agent_runs(id),
                created_at       TIMESTAMP    DEFAULT NOW(),
                UNIQUE (sku, store_id, valid_from)
            )
        """),
        ("idx context_adjustments dates", "CREATE INDEX IF NOT EXISTS idx_context_adjustments_dates ON inv.context_adjustments(valid_from, valid_to)"),
        ("idx context_adjustments store_sku", "CREATE INDEX IF NOT EXISTS idx_context_adjustments_store_sku ON inv.context_adjustments(store_id, sku)"),
    ]

    for label, sql in tables:
        run(label, sql, c)

    c.close()


# ══════════════════════════════════════════════════════════════
# 2. Peupler inv.stores depuis public.boutiques + sales_history.csv
# ══════════════════════════════════════════════════════════════
def populate_stores():
    logger.info("\n=== 2. Peupler inv.stores ===")
    c = conn()
    inserted = 0

    # Depuis public.boutiques
    with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT store_id, store_name, ville FROM public.boutiques WHERE statut='A'")
        boutiques = cur.fetchall()

    for b in boutiques:
        try:
            with c.cursor() as cur:
                cur.execute("""
                    INSERT INTO inv.stores (store_id, store_name, region, active)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (store_id) DO NOTHING
                """, (b['store_id'], b['store_name'] or b['store_id'], b['ville'] or 'Tunisie'))
            c.commit()
            inserted += 1
        except Exception as e:
            c.rollback()

    # Depuis sales_history.csv si présent
    sales_csv = INV_DATA / "sales_history.csv"
    if sales_csv.exists():
        try:
            df = pd.read_csv(sales_csv, usecols=['store_id', 'store_name'],
                             dtype=str).drop_duplicates('store_id')
            for _, row in df.iterrows():
                sid = str(row.get('store_id', '')).strip()
                sname = str(row.get('store_name', sid)).strip()
                if sid:
                    try:
                        with c.cursor() as cur:
                            cur.execute("""
                                INSERT INTO inv.stores (store_id, store_name, active)
                                VALUES (%s, %s, TRUE)
                                ON CONFLICT (store_id) DO NOTHING
                            """, (sid, sname))
                        c.commit()
                        inserted += 1
                    except Exception:
                        c.rollback()
        except Exception as e:
            logger.warning(f"  sales_history.csv stores: {e}")

    c.close()
    logger.info(f"  → inv.stores: {inserted} insérés")


# ══════════════════════════════════════════════════════════════
# 3. Peupler inv.products depuis product_master.csv + public.produits
# ══════════════════════════════════════════════════════════════
def populate_products():
    logger.info("\n=== 3. Peupler inv.products ===")
    c = conn()
    inserted = 0

    # product_master.csv
    prod_csv = INV_DATA / "product_master.csv"
    if prod_csv.exists():
        try:
            df = pd.read_csv(prod_csv, dtype=str, low_memory=False)
            logger.info(f"  product_master.csv: {len(df)} lignes | colonnes: {df.columns.tolist()[:8]}")

            for _, row in df.iterrows():
                sku = str(row.get('sku', row.get('SKU', row.get('cod_prod', '')))).strip()
                if not sku or sku == 'nan':
                    continue

                name = str(row.get('product_name', row.get('des_prod', sku))).strip()
                cat  = str(row.get('category', row.get('categorie', 'Autre'))).strip()
                uc   = _safe_float(row.get('unit_cost', row.get('pa_ttc', 0)))
                up   = _safe_float(row.get('unit_price', row.get('pv_ttc', 0)))
                ltd  = _safe_int(row.get('lead_time_days', 7))
                lts  = _safe_float(row.get('lead_time_std', 1.0))
                moq  = _safe_int(row.get('moq', 1))
                lc   = str(row.get('lifecycle_stage', 'mature')).strip()
                if lc not in ('growth','mature','decline','discontinued'):
                    lc = 'mature'

                try:
                    with c.cursor() as cur:
                        cur.execute("""
                            INSERT INTO inv.products
                                (sku, product_name, category, unit_cost, unit_price,
                                 lead_time_days, lead_time_std, moq, lifecycle_stage)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (sku) DO UPDATE SET
                                product_name   = EXCLUDED.product_name,
                                category       = EXCLUDED.category,
                                unit_cost      = EXCLUDED.unit_cost,
                                unit_price     = EXCLUDED.unit_price,
                                lead_time_days = EXCLUDED.lead_time_days,
                                lead_time_std  = EXCLUDED.lead_time_std,
                                updated_at     = NOW()
                        """, (sku, name[:200], cat[:100], uc, up, ltd, lts, moq, lc))
                    c.commit()
                    inserted += 1
                except Exception as e:
                    c.rollback()

            logger.info(f"  product_master.csv → {inserted} produits")
        except Exception as e:
            logger.warning(f"  product_master.csv: {e}")

    # Complément depuis public.produits
    try:
        with conn() as c2:
            with c2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT cod_prod, des_prod, categorie, pa_ttc, pv_ttc, actif FROM public.produits")
                produits = cur.fetchall()

        c3 = conn()
        for p in produits:
            sku  = str(p['cod_prod']).strip()
            name = str(p['des_prod'] or sku).strip()
            cat  = str(p['categorie'] or 'Autre').strip()
            uc   = float(p['pa_ttc'] or 0)
            up   = float(p['pv_ttc'] or 0)
            lc   = 'mature' if p['actif'] == 'O' else 'discontinued'
            try:
                with c3.cursor() as cur:
                    cur.execute("""
                        INSERT INTO inv.products
                            (sku, product_name, category, unit_cost, unit_price, lifecycle_stage)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (sku) DO NOTHING
                    """, (sku, name[:200], cat[:100], uc, up, lc))
                c3.commit()
                inserted += 1
            except Exception:
                c3.rollback()
        c3.close()
        logger.info(f"  public.produits complément → total {inserted} produits")
    except Exception as e:
        logger.warning(f"  public.produits: {e}")


# ══════════════════════════════════════════════════════════════
# 4. Peupler inv.stock_levels depuis stock_history.csv + public.stock
# ══════════════════════════════════════════════════════════════
def populate_stock_levels():
    logger.info("\n=== 4. Peupler inv.stock_levels ===")
    c = conn()
    inserted = 0

    # stock_history.csv — prendre la dernière ligne par (sku, store_id)
    stock_csv = INV_DATA / "stock_history.csv"
    if stock_csv.exists():
        try:
            df = pd.read_csv(stock_csv, dtype=str, low_memory=False)
            logger.info(f"  stock_history.csv: {len(df)} lignes | colonnes: {df.columns.tolist()[:8]}")

            # Normaliser les noms de colonnes
            df.columns = [c.lower().strip() for c in df.columns]

            sku_col   = next((c for c in df.columns if 'sku' in c or 'cod_prod' in c or 'product' in c), None)
            store_col = next((c for c in df.columns if 'store' in c), None)
            qty_col   = next((c for c in df.columns if 'stock' in c and 'hist' not in c), None)
            date_col  = next((c for c in df.columns if 'date' in c), None)

            logger.info(f"  Colonnes détectées: sku={sku_col} store={store_col} qty={qty_col} date={date_col}")

            if sku_col and store_col:
                if date_col:
                    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
                    df = df.sort_values(date_col).groupby([sku_col, store_col]).last().reset_index()
                else:
                    df = df.groupby([sku_col, store_col]).last().reset_index()

                for _, row in df.iterrows():
                    sku      = str(row[sku_col]).strip()
                    store_id = str(row[store_col]).strip()
                    qty      = _safe_int(row.get(qty_col, 0)) if qty_col else 0

                    if not sku or not store_id or sku == 'nan' or store_id == 'nan':
                        continue

                    try:
                        with c.cursor() as cur:
                            cur.execute("""
                                INSERT INTO inv.stock_levels
                                    (sku, store_id, stock_current, stock_min, stock_max)
                                VALUES (%s, %s, %s, 5, 200)
                                ON CONFLICT (sku, store_id) DO UPDATE SET
                                    stock_current = EXCLUDED.stock_current,
                                    last_updated  = NOW()
                            """, (sku, store_id, qty))
                        c.commit()
                        inserted += 1
                    except Exception:
                        c.rollback()

            logger.info(f"  stock_history.csv → {inserted} niveaux stock")
        except Exception as e:
            logger.warning(f"  stock_history.csv: {e}")

    # Complément depuis public.stock (cod_prod → sku)
    try:
        with conn() as c2:
            with c2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT s.store_id, s.cod_prod, s.qte_stk
                    FROM public.stock s
                    WHERE s.cod_prod IS NOT NULL
                      AND s.store_id IS NOT NULL
                      AND EXISTS (SELECT 1 FROM inv.products p WHERE p.sku = s.cod_prod)
                      AND EXISTS (SELECT 1 FROM inv.stores  st WHERE st.store_id = s.store_id)
                """)
                stocks = cur.fetchall()

        c3 = conn()
        for s in stocks:
            try:
                with c3.cursor() as cur:
                    cur.execute("""
                        INSERT INTO inv.stock_levels
                            (sku, store_id, stock_current, stock_min, stock_max)
                        VALUES (%s, %s, %s, 5, 200)
                        ON CONFLICT (sku, store_id) DO UPDATE SET
                            stock_current = GREATEST(inv.stock_levels.stock_current,
                                                     EXCLUDED.stock_current),
                            last_updated  = NOW()
                    """, (str(s['cod_prod']), str(s['store_id']), int(s['qte_stk'] or 0)))
                c3.commit()
                inserted += 1
            except Exception:
                c3.rollback()
        c3.close()
        logger.info(f"  public.stock complément → total {inserted} stock_levels")
    except Exception as e:
        logger.warning(f"  public.stock: {e}")

    c.close()


# ══════════════════════════════════════════════════════════════
# 5. Peupler inv.promotions depuis promotions.csv
# ══════════════════════════════════════════════════════════════
def populate_promotions():
    logger.info("\n=== 5. Peupler inv.promotions ===")
    promo_csv = INV_DATA / "promotions.csv"
    if not promo_csv.exists():
        logger.warning(f"  {promo_csv} non trouvé — insertion d'exemples")
        _insert_sample_promotions()
        return

    try:
        df = pd.read_csv(promo_csv, dtype=str)
        logger.info(f"  promotions.csv: {len(df)} lignes | colonnes: {df.columns.tolist()}")
        df.columns = [c.lower().strip() for c in df.columns]

        c = conn()
        inserted = 0
        for i, row in df.iterrows():
            promo_id = str(row.get('promo_id', f'PROMO_{i+1:04d}')).strip()
            name     = str(row.get('promo_name', row.get('name', f'Promo {i+1}'))).strip()
            ptype    = str(row.get('promo_type', 'discount')).strip()
            sd       = _safe_date(row.get('start_date'))
            ed       = _safe_date(row.get('end_date'))
            sku      = str(row.get('sku', '')).strip() or None
            cat      = str(row.get('category', '')).strip() or None
            disc     = _safe_float(row.get('discount_pct', 0))
            scope    = str(row.get('scope', 'national')).strip()
            if scope not in ('national','regional','store'):
                scope = 'national'
            active   = str(row.get('is_active', 'false')).lower() in ('true','1','yes','oui')

            if sd is None or ed is None:
                continue

            try:
                with c.cursor() as cur:
                    cur.execute("""
                        INSERT INTO inv.promotions
                            (promo_id, promo_name, promo_type, start_date, end_date,
                             sku, category, discount_pct, scope, is_active)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (promo_id) DO NOTHING
                    """, (promo_id, name[:200], ptype, sd, ed, sku, cat, disc, scope, active))
                c.commit()
                inserted += 1
            except Exception as e:
                c.rollback()

        c.close()
        logger.info(f"  → inv.promotions: {inserted} promotions")
    except Exception as e:
        logger.warning(f"  promotions.csv: {e}")
        _insert_sample_promotions()


def _insert_sample_promotions():
    """Promotions Ooredoo statiques si pas de CSV."""
    promos = [
        ('PROMO_5G_001', 'Forfait 5G Max — Offre découverte', 'discount',
         '2026-01-01', '2026-12-31', None, 'Forfait', 10.0, 'national', True),
        ('PROMO_FIBRE_001', 'Box Fibre 1Go — Installation offerte', 'bundle',
         '2026-01-01', '2026-12-31', None, 'Box Fibre', 0.0, 'national', True),
        ('PROMO_IPHONE_001', 'iPhone 16 Pro — Avance postpayé', 'discount',
         '2026-03-01', '2026-06-30', None, 'Terminal', 5.0, 'national', True),
    ]
    c = conn()
    for p in promos:
        try:
            with c.cursor() as cur:
                cur.execute("""
                    INSERT INTO inv.promotions
                        (promo_id, promo_name, promo_type, start_date, end_date,
                         sku, category, discount_pct, scope, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (promo_id) DO NOTHING
                """, p)
            c.commit()
        except Exception:
            c.rollback()
    c.close()
    logger.info("  → inv.promotions: 3 promotions exemple insérées")


# ══════════════════════════════════════════════════════════════
# 6. Peupler inv.events (événements Tunisie 2026)
# ══════════════════════════════════════════════════════════════
def populate_events():
    logger.info("\n=== 6. Peupler inv.events ===")
    events = [
        ('Ramadan 2026',     'religious',  '2026-02-17', '2026-03-18', ['Forfait','Recharge'], 25.0),
        ('Eid El Fitr 2026', 'religious',  '2026-03-19', '2026-03-22', ['Terminal','Accessoire'], 40.0),
        ('Eid El Adha 2026', 'religious',  '2026-05-26', '2026-05-30', ['Terminal','Forfait'], 35.0),
        ('Fête République',  'holiday',    '2026-07-25', '2026-07-26', ['Recharge'], 10.0),
        ('Rentrée 2026',     'seasonal',   '2026-09-01', '2026-09-15', ['Terminal','Forfait'], 20.0),
        ('Black Friday',     'promotional','2026-11-27', '2026-11-30', ['Terminal','Accessoire'], 30.0),
        ('Fêtes de fin année','seasonal',  '2026-12-20', '2026-12-31', ['Terminal','Accessoire'], 25.0),
    ]
    c = conn()
    inserted = 0
    for name, etype, sd, ed, cats, uplift in events:
        try:
            import json
            with c.cursor() as cur:
                cur.execute("""
                    INSERT INTO inv.events
                        (event_name, event_type, start_date, end_date,
                         affected_categories, estimated_uplift_pct, scope)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s,'national')
                    ON CONFLICT DO NOTHING
                """, (name, etype, sd, ed, json.dumps(cats), uplift))
            c.commit()
            inserted += 1
        except Exception as e:
            c.rollback()
    c.close()
    logger.info(f"  → inv.events: {inserted} événements")


# ══════════════════════════════════════════════════════════════
# 7. Peupler inv.business_objectives
# ══════════════════════════════════════════════════════════════
def populate_objectives():
    logger.info("\n=== 7. Peupler inv.business_objectives ===")
    objectives = [
        ('maximize_service_level', 1, 0.95, True),
        ('minimize_cost',          2, 0.80, False),
        ('prioritize_margin',      3, 0.90, False),
        ('clear_stock',            4, 0.70, False),
    ]
    c = conn()
    inserted = 0
    for otype, prio, target, active in objectives:
        try:
            with c.cursor() as cur:
                cur.execute("""
                    INSERT INTO inv.business_objectives
                        (objective_type, priority, target_value, applies_to, is_active)
                    VALUES (%s,%s,%s,'all',%s)
                """, (otype, prio, target, active))
            c.commit()
            inserted += 1
        except Exception as e:
            c.rollback()
    c.close()
    logger.info(f"  → inv.business_objectives: {inserted} objectifs")


# ══════════════════════════════════════════════════════════════
# 8. Peupler inv.demand_forecast depuis timesFM_future_forecast.csv
# ══════════════════════════════════════════════════════════════
def populate_forecast():
    logger.info("\n=== 8. Peupler inv.demand_forecast ===")
    fc_csv = FORECAST / "timesFM_future_forecast.csv"
    if not fc_csv.exists():
        logger.warning(f"  {fc_csv} non trouvé — skip")
        return

    try:
        df = pd.read_csv(fc_csv, dtype=str, low_memory=False)
        logger.info(f"  timesFM_future_forecast.csv: {len(df)} lignes | colonnes: {df.columns.tolist()[:8]}")
        df.columns = [c.lower().strip() for c in df.columns]

        sku_col   = next((c for c in df.columns if 'sku' in c), None)
        store_col = next((c for c in df.columns if 'store' in c), None)
        date_col  = next((c for c in df.columns if 'date' in c), None)
        dem_col   = next((c for c in df.columns if 'demand' in c or 'forecast' in c or 'qty' in c), None)

        if not all([sku_col, store_col, date_col, dem_col]):
            logger.warning(f"  Colonnes manquantes: {df.columns.tolist()}")
            return

        c = conn()
        inserted = 0
        for _, row in df.head(10000).iterrows():  # limiter à 10k
            sku      = str(row.get(sku_col, '')).strip()
            store_id = str(row.get(store_col, '')).strip()
            fdate    = _safe_date(row.get(date_col))
            demand   = _safe_float(row.get(dem_col, 0))

            if not sku or not store_id or not fdate:
                continue

            try:
                with c.cursor() as cur:
                    cur.execute("""
                        INSERT INTO inv.demand_forecast
                            (sku, store_id, forecast_date, demand_24h, model_version)
                        VALUES (%s,%s,%s,%s,'TimesFM-1.0')
                        ON CONFLICT (sku, store_id, forecast_date) DO NOTHING
                    """, (sku, store_id, fdate, demand))
                c.commit()
                inserted += 1
            except Exception:
                c.rollback()

        c.close()
        logger.info(f"  → inv.demand_forecast: {inserted} prévisions")
    except Exception as e:
        logger.warning(f"  timesFM_future_forecast.csv: {e}")


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════
def _safe_float(v, default=0.0):
    try:
        return float(str(v).replace(',','.').strip())
    except Exception:
        return default

def _safe_int(v, default=0):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default

def _safe_date(v):
    if v is None or str(v).strip() in ('', 'nan', 'NaT', 'None'):
        return None
    try:
        return pd.to_datetime(str(v)).date()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# Vérification finale
# ══════════════════════════════════════════════════════════════
def verify():
    logger.info("\n=== VÉRIFICATION FINALE ===")
    c = conn()
    with c.cursor() as cur:
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname='inv' ORDER BY tablename
        """)
        tables = [r[0] for r in cur.fetchall()]
        logger.info(f"Tables inv ({len(tables)}): {tables}")

        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM inv.{t}")
                n = cur.fetchone()[0]
                s = "✓" if n > 0 else "○"
                logger.info(f"  {s} inv.{t}: {n:,}")
            except Exception as e:
                logger.warning(f"  ✗ inv.{t}: {e}")
    c.close()


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║  SETUP INV SCHEMA — AI Sales Coach Ooredoo          ║")
    logger.info("╚══════════════════════════════════════════════════════╝")

    create_schema()
    populate_stores()
    populate_products()
    populate_stock_levels()
    populate_promotions()
    populate_events()
    populate_objectives()
    populate_forecast()
    verify()

    logger.info("\n✅ Setup inv terminé — relancez: uvicorn main:app --port 8000")