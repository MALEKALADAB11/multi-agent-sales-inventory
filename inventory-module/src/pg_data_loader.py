"""
pg_data_loader.py — PostgreSQL Data Loader pour inventory-module
==================================================================
Remplace TOUS les read_csv de l'inventory-module par des lectures PostgreSQL.
Drop-in replacement : memes signatures, memes retours (DataFrame/dict/list).

Usage dans les fichiers existants :
  AVANT: from config.settings import STOCK_HISTORY_PATH, SALES_HISTORY_PATH
         df = pd.read_csv(STOCK_HISTORY_PATH)
  APRES: from pg_data_loader import load_stock_history
         df = load_stock_history(store_id="I63")

Couvrir : stock_history, sales_history, product_master, promotions,
          SKU listing, store listing.
"""

import os
import logging
from typing import Optional, List

import pandas as pd
import psycopg2
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "admin")),
}

_pool: ThreadedConnectionPool = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(2, 10, **DB_CONFIG, connect_timeout=10)
    return _pool


def _conn():
    return _get_pool().getconn()


def _release(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        logger.warning(f"[PG_LOADER] {e}")
        return pd.DataFrame()
    finally:
        _release(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 1. STOCK HISTORY
# ══════════════════════════════════════════════════════════════════════════════

def load_stock_history(store_id: str = None, sku: int = None, days: int = 365) -> pd.DataFrame:
    """Remplace pd.read_csv(STOCK_HISTORY_PATH)."""
    where = ["record_date >= CURRENT_DATE - %(days)s * INTERVAL '1 day'"]
    params = {"days": days}
    if store_id:
        where.append("store_id = %(store_id)s")
        params["store_id"] = store_id
    if sku:
        where.append("sku = %(sku)s")
        params["sku"] = int(sku)

    sql = f"""
        SELECT record_date AS date, store_id, store_name, region,
               sku, product_name, category, stock_level, is_stockout
        FROM inventory.stock_history
        WHERE {' AND '.join(where)}
        ORDER BY record_date
    """
    df = _read_sql(sql, params)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ══════════════════════════════════════════════════════════════════════════════
# 2. SALES HISTORY
# ══════════════════════════════════════════════════════════════════════════════

def load_sales_history(store_id: str = None, sku: int = None, days: int = 365) -> pd.DataFrame:
    """Charge l'historique ventes avec toutes les features temporelles et saisonnières."""
    where = ["record_date >= CURRENT_DATE - %(days)s * INTERVAL '1 day'"]
    params = {"days": days}
    if store_id:
        where.append("store_id = %(store_id)s")
        params["store_id"] = store_id
    if sku:
        where.append("sku = %(sku)s")
        params["sku"] = int(sku)

    sql = f"""
        SELECT record_date AS date, store_id, sku, product_name, category,
               quantity_sold, revenue, unit_price,
               is_promo, promo_type, event_name, event_type, season,
               COALESCE(is_event_day, FALSE)    AS is_event_day,
               COALESCE(event_intensite, '')     AS event_intensite,
               COALESCE(uplift_factor, 1.0)      AS uplift_factor,
               COALESCE(is_weekend, FALSE)        AS is_weekend,
               COALESCE(day_of_week, 0)           AS day_of_week,
               COALESCE(week_of_year, 1)          AS week_of_year,
               COALESCE(month_num, 1)             AS month_num,
               COALESCE(year_num, 2024)           AS year_num
        FROM inventory.sales_history
        WHERE {' AND '.join(where)}
        ORDER BY record_date
    """
    df = _read_sql(sql, params)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_seasonal_demand_profile(sku: int, store_id: str, category: str = None) -> dict:
    """
    Profil de demande saisonnière complet depuis 3 ans d'historique.
    Utilisé par l'agent analyste série temporelle.

    Retourne :
    - baseline_demand   : demande journalière hors events, hors promo
    - by_month          : facteurs par mois vs baseline
    - by_event_type     : uplift par type d'événement vs baseline
    - by_season         : uplift par saison vs baseline
    - by_dow            : facteur par jour de semaine
    - trend_6m          : tendance 6 mois (pct change)
    - data_years        : nombre d'années d'historique disponibles
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Baseline : jours hors events, hors promo (3 ans)
            cur.execute("""
                SELECT AVG(quantity_sold) AS avg_base,
                       STDDEV(quantity_sold) AS std_base,
                       COUNT(*) AS n_days,
                       COUNT(DISTINCT year_num) AS n_years
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s
                  AND NOT COALESCE(is_promo, FALSE)
                  AND NOT COALESCE(is_event_day, FALSE)
                  AND quantity_sold > 0
            """, (int(sku), store_id))
            base_row = cur.fetchone()
            avg_base = float(base_row[0] or 1.0)
            std_base = float(base_row[1] or avg_base * 0.3)
            n_years  = int(base_row[3] or 1)

            # Facteurs par mois (saisonnalité annuelle)
            cur.execute("""
                SELECT month_num, AVG(quantity_sold) AS avg_qty
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s AND quantity_sold > 0
                  AND NOT COALESCE(is_promo, FALSE)
                GROUP BY month_num
                ORDER BY month_num
            """, (int(sku), store_id))
            by_month = {
                r[0]: round(float(r[1] or 0) / avg_base, 3)
                for r in cur.fetchall()
            }

            # Facteurs par type d'événement
            cur.execute("""
                SELECT event_type, AVG(quantity_sold) AS avg_qty, COUNT(*) AS n
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s
                  AND event_type IS NOT NULL AND quantity_sold > 0
                GROUP BY event_type
            """, (int(sku), store_id))
            by_event = {
                r[0]: {"uplift": round(float(r[1] or 0) / avg_base, 3), "n_days": int(r[2])}
                for r in cur.fetchall() if r[1]
            }

            # Facteurs par saison
            cur.execute("""
                SELECT season, AVG(quantity_sold) AS avg_qty
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s AND quantity_sold > 0
                  AND season IS NOT NULL
                GROUP BY season
            """, (int(sku), store_id))
            by_season = {
                r[0]: round(float(r[1] or 0) / avg_base, 3)
                for r in cur.fetchall() if r[1]
            }

            # Facteur par jour de semaine (0=Lundi ... 6=Dimanche)
            cur.execute("""
                SELECT day_of_week, AVG(quantity_sold) AS avg_qty
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s AND quantity_sold > 0
                  AND day_of_week IS NOT NULL
                  AND NOT COALESCE(is_event_day, FALSE)
                GROUP BY day_of_week
                ORDER BY day_of_week
            """, (int(sku), store_id))
            by_dow = {
                r[0]: round(float(r[1] or 0) / avg_base, 3)
                for r in cur.fetchall() if r[1]
            }

            # Tendance 6 mois vs 6 mois précédents
            cur.execute("""
                SELECT
                    AVG(CASE WHEN record_date >= CURRENT_DATE - INTERVAL '180 days'
                              AND record_date < CURRENT_DATE THEN quantity_sold END) AS recent_6m,
                    AVG(CASE WHEN record_date >= CURRENT_DATE - INTERVAL '360 days'
                              AND record_date < CURRENT_DATE - INTERVAL '180 days'
                              THEN quantity_sold END) AS prev_6m
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s AND quantity_sold > 0
            """, (int(sku), store_id))
            trend_row = cur.fetchone()
            recent, prev = float(trend_row[0] or 0), float(trend_row[1] or 0)
            trend_6m_pct = round((recent - prev) / prev * 100, 1) if prev > 0 else 0.0

            return {
                "sku":              int(sku),
                "store_id":         store_id,
                "baseline_demand":  round(avg_base, 4),
                "demand_std":       round(std_base, 4),
                "n_data_years":     n_years,
                "by_month":         by_month,
                "by_event_type":    by_event,
                "by_season":        by_season,
                "by_dow":           by_dow,
                "trend_6m_pct":     trend_6m_pct,
                "source":           "postgresql_3years",
            }
    except Exception as e:
        logger.warning(f"[PG_LOADER] seasonal profile error: {e}")
        return {
            "sku": int(sku), "baseline_demand": 0, "demand_std": 0,
            "n_data_years": 0, "by_month": {}, "by_event_type": {},
            "by_season": {}, "by_dow": {}, "trend_6m_pct": 0,
            "source": "error",
        }
    finally:
        _release(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 3. PRODUCT MASTER
# ══════════════════════════════════════════════════════════════════════════════

def load_product_master(sku: int = None) -> pd.DataFrame:
    """Remplace pd.read_csv(PRODUCT_MASTER_PATH)."""
    # p.categorie is a raw numeric code ("20", "30", "88"...) — not presentable.
    # p.gamme_libelle holds the human label seeded for exactly this code
    # (data/telco/seed_data.sql: 20→SIM_KIT, 88→FORFAIT, 50→TERMINAL, ...).
    # INITCAP + underscore→space turns "FORFAIT_BOX" into "Forfait Box".
    # Falls back to the raw code only if gamme_libelle was never seeded.
    _CATEGORY_SQL = "INITCAP(REPLACE(COALESCE(p.gamme_libelle, p.categorie), '_', ' ')) AS category"
    if sku:
        return _read_sql(f"""
            SELECT pm.sku, p.nom AS product_name, {_CATEGORY_SQL},
                   pm.unit_cost, pm.unit_price, pm.lead_time_days,
                   pm.lead_time_std, pm.moq, pm.holding_cost_pct,
                   pm.order_cost, pm.lifecycle_stage
            FROM inventory.products pm
            LEFT JOIN sales.produits p ON p.sku = pm.sku
            WHERE pm.sku = %(sku)s
        """, {"sku": int(sku)})
    return _read_sql(f"""
        SELECT pm.sku, p.nom AS product_name, {_CATEGORY_SQL},
               pm.unit_cost, pm.unit_price, pm.lead_time_days,
               pm.lead_time_std, pm.moq, pm.holding_cost_pct,
               pm.order_cost, pm.lifecycle_stage
        FROM inventory.products pm
        LEFT JOIN sales.produits p ON p.sku = pm.sku
    """)


# ══════════════════════════════════════════════════════════════════════════════
# 4. PROMOTIONS
# ══════════════════════════════════════════════════════════════════════════════

def load_promotions(active_only: bool = True) -> pd.DataFrame:
    """Remplace pd.read_csv(PROMOTIONS_PATH)."""
    if active_only:
        return _read_sql("""
            SELECT promo_id, promo_name, start_date, end_date,
                   sku, product_name, category, discount_pct, promo_type, scope
            FROM inventory.promotions
            WHERE end_date >= CURRENT_DATE OR end_date IS NULL
        """)
    return _read_sql("SELECT * FROM inventory.promotions")


# ══════════════════════════════════════════════════════════════════════════════
# 5. SKU LISTING — Pour routes.py et main.py
# ══════════════════════════════════════════════════════════════════════════════

def get_available_skus(store_id: str = None) -> List[int]:
    """Liste les SKUs avec du stock. Remplace pd.read_csv(STOCK_HISTORY).sku.unique()."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if store_id:
                cur.execute("""
                    SELECT DISTINCT sku FROM inventory.stock_levels
                    WHERE store_id = %s AND quantity > 0 ORDER BY sku
                """, (store_id,))
            else:
                cur.execute("""
                    SELECT DISTINCT sku FROM inventory.stock_levels
                    WHERE quantity > 0 ORDER BY sku
                """)
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[PG_LOADER] get_available_skus: {e}")
        return []
    finally:
        _release(conn)


def get_available_stores() -> List[str]:
    """Liste les stores actifs avec du stock."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT sl.store_id
                FROM inventory.stock_levels sl
                JOIN sales.boutiques b ON b.store_id = sl.store_id
                WHERE b.active = true AND sl.quantity > 0
                ORDER BY sl.store_id
            """)
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[PG_LOADER] get_available_stores: {e}")
        return []
    finally:
        _release(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 6. STOCK LEVELS — Snapshot actuel
# ══════════════════════════════════════════════════════════════════════════════

def load_stock_levels(store_id: str = None) -> pd.DataFrame:
    """Charge le snapshot stock actuel."""
    if store_id:
        return _read_sql("""
            SELECT sl.store_id, sl.sku, p.nom AS product_name, p.categorie AS category,
                   COALESCE(sl.quantity_available, sl.quantity, 0) AS quantity,
                   COALESCE(sl.quantity_reserved, 0) AS quantity_reserved,
                   COALESCE(sl.quantity_available, sl.quantity, 0) AS available,
                   COALESCE(sl.last_updated, sl.updated_at) AS last_updated,
                   sl.last_sold
            FROM inventory.stock_levels sl
            LEFT JOIN sales.produits p ON p.sku = sl.sku
            WHERE sl.store_id = %(store_id)s
            ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC
        """, {"store_id": store_id})
    return _read_sql("""
        SELECT sl.store_id, sl.sku, p.nom AS product_name, p.categorie AS category,
               COALESCE(sl.quantity_available, sl.quantity, 0) AS quantity,
               COALESCE(sl.quantity_reserved, 0) AS quantity_reserved,
               COALESCE(sl.quantity_available, sl.quantity, 0) AS available,
               COALESCE(sl.last_updated, sl.updated_at) AS last_updated,
               sl.last_sold
        FROM inventory.stock_levels sl
        LEFT JOIN sales.produits p ON p.sku = sl.sku
        ORDER BY sl.store_id, COALESCE(sl.quantity_available, sl.quantity, 0) ASC
    """)


# ══════════════════════════════════════════════════════════════════════════════
# 7. DEMAND ANALYSIS — Pour context agent
# ══════════════════════════════════════════════════════════════════════════════

def get_demand_uplift_data(sku: int, store_id: str, category: str = None) -> dict:
    """
    Calcule l'uplift de demande base sur l'historique.
    Utilise par context/tools.py pour calibrer les predictions.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Ventes moyennes hors promo
            cur.execute("""
                SELECT AVG(quantity_sold) AS avg_base,
                       STDDEV(quantity_sold) AS std_base,
                       COUNT(*) AS n_days
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s AND NOT is_promo
                  AND record_date >= CURRENT_DATE - INTERVAL '90 days'
            """, (int(sku), store_id))
            base = cur.fetchone()

            # Ventes moyennes en promo
            cur.execute("""
                SELECT AVG(quantity_sold) AS avg_promo, COUNT(*) AS n_promo_days
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s AND is_promo
                  AND record_date >= CURRENT_DATE - INTERVAL '90 days'
            """, (int(sku), store_id))
            promo = cur.fetchone()

            avg_base = float(base[0] or 0) if base else 0
            avg_promo = float(promo[0] or 0) if promo else 0
            n_days = int(base[2] or 0) if base else 0

            uplift_pct = 0.0
            if avg_base > 0 and avg_promo > 0:
                uplift_pct = round((avg_promo - avg_base) / avg_base * 100, 1)

            return {
                "sku": int(sku),
                "store_id": store_id,
                "avg_daily_demand": round(avg_base, 2),
                "avg_promo_demand": round(avg_promo, 2),
                "promo_uplift_pct": uplift_pct,
                "n_data_days": n_days,
                "source": "postgresql",
            }
    except Exception as e:
        logger.warning(f"[PG_LOADER] demand uplift error: {e}")
        return {"sku": int(sku), "avg_daily_demand": 0, "promo_uplift_pct": 0, "source": "error"}
    finally:
        _release(conn)