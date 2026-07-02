"""
stock_tools.py — v2.0 PostgreSQL-only
=======================================
Data layer pour l'inventory-module.
Lit TOUT depuis PostgreSQL — zero CSV.

Tables utilisees :
  inventory.stock_levels    — stock actuel par store/sku
  inventory.stock_history   — historique stock journalier
  inventory.sales_history   — historique ventes avec promos/events
  inventory.products        — couts, lead time, MOQ, lifecycle
  inventory.promotions      — promotions actives
  sales.produits            — catalogue produits
  sales.boutiques           — boutiques actives

Fonctions (meme signature que l'ancien pour compatibilite) :
  get_stock_level(sku, store_id)   -> dict
  get_product_info(sku)            -> dict
  get_sales_history(sku, store_id) -> DataFrame
  get_stock_history(sku, store_id) -> DataFrame
  get_forecast_data(sku, store_id) -> DataFrame
  apply_sale_override(...)         -> None
  get_all_skus_for_store(store_id) -> list[int]
"""

import os
import logging
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "admin")),
}

# In-memory sale overrides (from realtime simulator)
_sale_overrides: Dict[str, Dict[str, Any]] = {}

_pool: Optional[ThreadedConnectionPool] = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(2, 20, **DB_CONFIG, connect_timeout=10)
    return _pool


def _get_conn():
    return _get_pool().getconn()


def _release_conn(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def _query(sql: str, params: tuple = None, fetch: str = "all") -> Any:
    """Execute une requete et retourne les resultats."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return dict(cur.fetchone()) if cur.description and cur.rowcount else None
            elif fetch == "all":
                return [dict(r) for r in cur.fetchall()] if cur.description else []
            elif fetch == "val":
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.warning(f"[STOCK_TOOLS] Query error: {e}")
        return None if fetch in ("one", "val") else []
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 1. STOCK LEVEL — Niveau de stock actuel pour un SKU/store
# ══════════════════════════════════════════════════════════════════════════════

def get_stock_level(sku, store_id: str = "I63") -> Dict[str, Any]:
    """
    Retourne le niveau de stock pour un SKU dans un store.
    Applique les overrides du simulateur temps reel si presents.
    """
    sku = int(sku)
    override_key = f"{sku}@{store_id}"

    # Check overrides first (from realtime simulator)
    if override_key in _sale_overrides:
        ov = _sale_overrides[override_key]
        return {
            "sku": sku,
            "store_id": store_id,
            "stock_on_hand": max(0, ov.get("stock_on_hand", 0)),
            "stock_available": max(0, ov.get("stock_available", 0)),
            "stock_reserved": ov.get("stock_reserved", 0),
            "stock_in_transit": 0,
            "stock_min": None,
            "last_received": None,
            "last_sold": str(date.today()),
            "source": "override",
        }

    # PostgreSQL
    row = _query("""
        SELECT sl.quantity, COALESCE(sl.quantity_reserved, 0) AS reserved,
               sl.last_received, sl.last_sold
        FROM inventory.stock_levels sl
        WHERE sl.sku = %s AND sl.store_id = %s
    """, (sku, store_id), fetch="one")

    if row:
        qty = int(row["quantity"] or 0)
        res = int(row["reserved"] or 0)
        return {
            "sku": sku,
            "store_id": store_id,
            "stock_on_hand": qty,
            "stock_available": max(0, qty - res),
            "stock_reserved": res,
            "stock_in_transit": 0,
            "stock_min": None,
            "last_received": str(row["last_received"]) if row["last_received"] else None,
            "last_sold": str(row["last_sold"]) if row["last_sold"] else None,
            "source": "postgresql",
        }

    logger.warning("No stock data for %s@%s", sku, store_id)
    return {
        "sku": sku, "store_id": store_id,
        "stock_on_hand": 0, "stock_available": 0, "stock_reserved": 0,
        "stock_in_transit": 0, "stock_min": None,
        "last_received": None, "last_sold": None, "source": "none",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. PRODUCT INFO — Informations produit enrichies
# ══════════════════════════════════════════════════════════════════════════════

def get_product_info(sku) -> Dict[str, Any]:
    """Retourne les infos produit depuis produits + product_master."""
    sku = int(sku)

    row = _query("""
        SELECT p.sku, p.nom AS product_name, p.categorie AS category,
               p.famille AS family, p.prix_ht, p.prix_ttc AS unit_price,
               p.marge_pct, p.actif AS active,
               pm.unit_cost, pm.lead_time_days, pm.lead_time_std,
               pm.moq, pm.holding_cost_pct, pm.order_cost,
               pm.lifecycle_stage
        FROM sales.produits p
        LEFT JOIN inventory.products pm ON pm.sku = p.sku
        WHERE p.sku = %s
    """, (sku,), fetch="one")

    if row:
        return {
            "sku": sku,
            "product_name": row["product_name"] or f"SKU {sku}",
            "category": row["category"] or "",
            "family": row["family"] or "",
            "unit_cost": float(row["unit_cost"] or row["prix_ht"] or 0),
            "unit_price": float(row["unit_price"] or 0),
            "margin_pct": float(row["marge_pct"] or 0),
            "lead_time_days": int(row["lead_time_days"] or 10),
            "lead_time_std": int(row["lead_time_std"] or 3),
            "moq": int(row["moq"] or 1),
            "holding_cost_pct": float(row["holding_cost_pct"] or 0.25),
            "order_cost": float(row["order_cost"] or 50),
            "lifecycle_stage": row["lifecycle_stage"] or "growth",
            "active": bool(row["active"]),
            "source": "postgresql",
        }

    logger.warning("No product data for %s", sku)
    return {
        "sku": sku, "product_name": f"SKU {sku}", "category": "",
        "unit_cost": 0, "unit_price": 0, "lead_time_days": 10,
        "moq": 1, "lifecycle_stage": "unknown", "source": "none",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. SALES HISTORY — Historique ventes (pour series temporelles)
# ══════════════════════════════════════════════════════════════════════════════

def get_sales_history(sku, store_id: str = "I63", days: int = 365) -> pd.DataFrame:
    """
    Retourne l'historique des ventes depuis inventory.sales_history.
    Compatible TimesFM/Prophet : colonnes date, quantity_sold, revenue, is_promo.
    """
    sku = int(sku)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT record_date AS date, quantity_sold, revenue,
                       unit_price, is_promo, event_name, event_type, season
                FROM inventory.sales_history
                WHERE sku = %s AND store_id = %s
                  AND record_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY record_date
            """, (sku, store_id, days))
            cols = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        logger.warning("Sales history error for %s@%s: %s", sku, store_id, e)
        return pd.DataFrame(columns=["date", "quantity_sold", "revenue", "is_promo"])
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 4. STOCK HISTORY — Historique niveaux de stock
# ══════════════════════════════════════════════════════════════════════════════

def get_stock_history(sku, store_id: str = "I63", days: int = 365) -> pd.DataFrame:
    """
    Retourne l'historique stock depuis inventory.stock_history.
    Compatible series temporelles : colonnes date, stock_level, is_stockout.
    """
    sku = int(sku)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT record_date AS date, stock_level, is_stockout
                FROM inventory.stock_history
                WHERE sku = %s AND store_id = %s
                  AND record_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY record_date
            """, (sku, store_id, days))
            cols = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        logger.warning("Stock history error for %s@%s: %s", sku, store_id, e)
        return pd.DataFrame(columns=["date", "stock_level", "is_stockout"])
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 5. FORECAST DATA — Donnees pour prediction (sales + stock combinees)
# ══════════════════════════════════════════════════════════════════════════════

def get_forecast_data(sku, store_id: str = "I63", days: int = 180) -> pd.DataFrame:
    """
    Combine sales_history et stock_history pour le forecasting.
    Retourne un DataFrame journalier avec quantity_sold, stock_level, is_promo.
    """
    sku = int(sku)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(s.record_date, sh.record_date) AS date,
                    COALESCE(s.quantity_sold, 0) AS quantity_sold,
                    COALESCE(s.revenue, 0) AS revenue,
                    COALESCE(sh.stock_level, 0) AS stock_level,
                    COALESCE(s.is_promo, false) AS is_promo,
                    s.event_name, s.season
                FROM inventory.sales_history s
                FULL OUTER JOIN inventory.stock_history sh
                    ON s.sku = sh.sku AND s.store_id = sh.store_id AND s.record_date = sh.record_date
                WHERE COALESCE(s.sku, sh.sku) = %s
                  AND COALESCE(s.store_id, sh.store_id) = %s
                  AND COALESCE(s.record_date, sh.record_date) >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY date
            """, (sku, store_id, days))
            cols = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        logger.warning("Forecast data error for %s@%s: %s", sku, store_id, e)
        return pd.DataFrame(columns=["date", "quantity_sold", "stock_level", "is_promo"])
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 6. ALL SKUs FOR STORE — Liste des SKUs avec stock pour un store
# ══════════════════════════════════════════════════════════════════════════════

def get_all_skus_for_store(store_id: str = "I63") -> List[int]:
    """Retourne la liste des SKUs avec du stock pour un store."""
    rows = _query("""
        SELECT DISTINCT sl.sku
        FROM inventory.stock_levels sl
        WHERE sl.store_id = %s AND sl.quantity > 0
        ORDER BY sl.sku
    """, (store_id,))
    return [int(r["sku"]) for r in rows] if rows else []


# ══════════════════════════════════════════════════════════════════════════════
# 7. SALE OVERRIDE — Simulateur temps reel
# ══════════════════════════════════════════════════════════════════════════════

def apply_sale_override(sku, store_id: str, quantity_sold: int = 1):
    """
    Applique une vente simulee en memoire.
    Le stock reel dans PostgreSQL n'est pas modifie (read-only pour le simulateur).
    """
    sku = int(sku)
    key = f"{sku}@{store_id}"
    current = get_stock_level(sku, store_id)

    new_qty = max(0, current["stock_on_hand"] - quantity_sold)
    _sale_overrides[key] = {
        "stock_on_hand": new_qty,
        "stock_available": max(0, new_qty - current["stock_reserved"]),
        "stock_reserved": current["stock_reserved"],
        "last_sold": str(date.today()),
    }
    logger.debug("Sale override: %s qty=%d -> stock=%d", key, quantity_sold, new_qty)


def clear_overrides():
    """Reset les overrides (ex: nouveau jour)."""
    _sale_overrides.clear()
    logger.info("[STOCK_TOOLS] Overrides cleared")


# ══════════════════════════════════════════════════════════════════════════════
# 8. PROMOTIONS — Promotions actives pour un SKU
# ══════════════════════════════════════════════════════════════════════════════

def get_active_promotions(sku: int = None) -> List[Dict]:
    """Retourne les promotions actives, optionnellement filtrees par SKU."""
    if sku:
        return _query("""
            SELECT promo_id, promo_name, start_date, end_date,
                   sku, product_name, discount_pct, promo_type
            FROM inventory.promotions
            WHERE sku = %s AND (end_date >= CURRENT_DATE OR end_date IS NULL)
        """, (int(sku),))
    else:
        return _query("""
            SELECT promo_id, promo_name, start_date, end_date,
                   sku, product_name, discount_pct, promo_type
            FROM inventory.promotions
            WHERE end_date >= CURRENT_DATE OR end_date IS NULL
        """)


# ══════════════════════════════════════════════════════════════════════════════
# 9. BATCH — Donnees pre-chargees pour le pipeline batch
# ══════════════════════════════════════════════════════════════════════════════

def prefetch_store_data(store_id: str) -> Dict[str, Any]:
    """
    Pre-charge toutes les donnees pour un store en une seule passe.
    Utilise par l'orchestrateur pour eviter N+1 queries.
    """
    skus = get_all_skus_for_store(store_id)

    # Stock levels en batch
    stock_rows = _query("""
        SELECT sl.sku, sl.quantity, COALESCE(sl.quantity_reserved, 0) AS reserved,
               sl.last_received, sl.last_sold
        FROM inventory.stock_levels sl
        WHERE sl.store_id = %s
    """, (store_id,))
    stock_map = {int(r["sku"]): r for r in stock_rows} if stock_rows else {}

    # Products en batch
    if skus:
        placeholders = ",".join(["%s"] * len(skus))
        product_rows = _query(f"""
            SELECT p.sku, p.nom AS product_name, p.categorie AS category,
                   pm.unit_cost, pm.unit_price, pm.lead_time_days, pm.moq, pm.lifecycle_stage
            FROM sales.produits p
            LEFT JOIN inventory.products pm ON pm.sku = p.sku
            WHERE p.sku IN ({placeholders})
        """, tuple(skus))
        product_map = {int(r["sku"]): r for r in product_rows} if product_rows else {}
    else:
        product_map = {}

    return {
        "store_id": store_id,
        "skus": skus,
        "stock": stock_map,
        "products": product_map,
        "nb_skus": len(skus),
        "nb_ruptures": sum(1 for s in stock_map.values() if int(s.get("stock_available", s.get("stock_on_hand", 0)) or 0) <= 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# COMPATIBILITY SHIM — _DataCache for routes.py
# ══════════════════════════════════════════════════════════════════════════════
# routes.py importe `from src.tools.internal.stock_tools import _DataCache`
# et appelle _DataCache.stock(), .sales(), .product(), .forecast(), .invalidate()
# Ce shim redirige tout vers PostgreSQL via pg_data_loader.

class _DataCache:
    """Compatibility wrapper — reads from PostgreSQL, results cached 5 min in memory."""

    _stock_overrides: Dict = {}
    _cache: Dict[str, Any] = {}
    _ts: Dict[str, float] = {}
    _TTL: float = 300.0  # 5 minutes

    @classmethod
    def _fresh(cls, key: str) -> bool:
        return key in cls._cache and (_time.time() - cls._ts.get(key, 0)) < cls._TTL

    @classmethod
    def stock(cls) -> pd.DataFrame:
        if cls._fresh("stock"):
            return cls._cache["stock"]
        try:
            from pg_data_loader import load_stock_history
        except ImportError:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
            from src.pg_data_loader import load_stock_history
        df = load_stock_history()
        cls._cache["stock"] = df; cls._ts["stock"] = _time.time()
        return df

    @classmethod
    def sales(cls) -> pd.DataFrame:
        if cls._fresh("sales"):
            return cls._cache["sales"]
        try:
            from pg_data_loader import load_sales_history
        except ImportError:
            from src.pg_data_loader import load_sales_history
        df = load_sales_history()
        cls._cache["sales"] = df; cls._ts["sales"] = _time.time()
        return df

    @classmethod
    def product(cls) -> pd.DataFrame:
        if cls._fresh("product"):
            return cls._cache["product"]
        try:
            from pg_data_loader import load_product_master
        except ImportError:
            from src.pg_data_loader import load_product_master
        df = load_product_master()
        cls._cache["product"] = df; cls._ts["product"] = _time.time()
        return df

    @classmethod
    def forecast(cls) -> pd.DataFrame:
        if cls._fresh("forecast"):
            return cls._cache["forecast"]
        try:
            from pg_data_loader import load_sales_history
        except Exception:
            return pd.DataFrame(columns=["sku", "store_id", "predicted_demand"])
        try:
            df = load_sales_history(days=30)
            if not df.empty and "quantity_sold" in df.columns:
                df = df.rename(columns={"quantity_sold": "predicted_demand"})
            cls._cache["forecast"] = df; cls._ts["forecast"] = _time.time()
            return df
        except Exception:
            return pd.DataFrame(columns=["sku", "store_id", "predicted_demand"])

    @classmethod
    def invalidate(cls):
        cls._stock_overrides.clear()
        cls._cache.clear()
        cls._ts.clear()


# ══════════════════════════════════════════════════════════════════════════════
# COMPATIBILITY ALIASES — pour analysis/nodes.py, context/tools.py
# ══════════════════════════════════════════════════════════════════════════════

def get_stock_status(sku, store_id: str = "I63") -> Dict[str, Any]:
    """Alias de get_stock_level pour compatibilite analysis/nodes.py."""
    data = get_stock_level(sku, store_id)
    return {
        "sku": data["sku"],
        "store_id": data["store_id"],
        "current_stock": data["stock_on_hand"],
        "stock_available": data["stock_available"],
        "stock_reserved": data["stock_reserved"],
        "stock_in_transit": data.get("stock_in_transit", 0),
        "stock_min": data.get("stock_min"),
        "last_received": data.get("last_received"),
        "last_sold": data.get("last_sold"),
        "source": data["source"],
    }


def get_product(sku) -> Dict[str, Any]:
    """Alias de get_product_info pour compatibilite analysis/nodes.py."""
    return get_product_info(sku)


def get_forecast(sku, store_id: str = "I63") -> pd.DataFrame:
    """Alias de get_forecast_data pour compatibilite analysis/nodes.py."""
    df = get_forecast_data(sku, store_id)
    # analysis/nodes.py attend 'predicted_demand', pas 'quantity_sold'
    if not df.empty and "quantity_sold" in df.columns:
        df = df.rename(columns={"quantity_sold": "predicted_demand"})
    elif df.empty:
        df = pd.DataFrame({
            "date": pd.date_range(start=pd.Timestamp.now(), periods=30, freq="D"),
            "predicted_demand": [1.0] * 30,
            "sku": [str(sku)] * 30,
            "store_id": [store_id] * 30,
        })
    if "predicted_demand" not in df.columns:
        df["predicted_demand"] = 1.0
    return df