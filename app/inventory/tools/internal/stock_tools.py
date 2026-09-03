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

import logging
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from app.core.config import DEFAULT_STORE_ID
from app.core import db as core_db

logger = logging.getLogger(__name__)

# In-memory sale overrides (from realtime simulator)
_sale_overrides: Dict[str, Dict[str, Any]] = {}


# Connexions : pool partagé app.core.db (un seul pool pour tout le process,
# borné — évite de saturer max_connections côté serveur pendant les batchs).

def _get_conn():
    return core_db.getconn()


def _release_conn(conn):
    core_db.putconn(conn)


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

def get_stock_level(sku, store_id: str = DEFAULT_STORE_ID) -> Dict[str, Any]:
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

def _get_supplier_context(sku: int) -> Dict[str, Any]:
    """
    Fournisseur préféré + fallback pour ce SKU, depuis
    supply.supplier_products + supply.suppliers. Lecture seule, aucune
    jointure/index nouveau — SELECT existant uniquement (voir plan §Rules).
    """
    rows = _query("""
        SELECT sp.supplier_id, s.nom AS supplier_name,
               s.taux_fiabilite, s.actif AS supplier_actif,
               s.commande_multiple, sp.lead_time_days AS sp_lead_time
        FROM supply.supplier_products sp
        JOIN supply.suppliers s ON s.supplier_id = sp.supplier_id
        WHERE sp.sku = %s
        ORDER BY sp.is_preferred DESC, s.taux_fiabilite DESC
        LIMIT 2
    """, (sku,), fetch="all") or []

    if not rows:
        return {
            "preferred_supplier_id": None, "preferred_supplier_name": None,
            "preferred_supplier_reliable": True, "preferred_supplier_active": True,
            "supplier_order_multiple": 1,
            "fallback_supplier_name": None, "fallback_supplier_active": False,
        }

    preferred = rows[0]
    fallback  = rows[1] if len(rows) > 1 else None

    return {
        "preferred_supplier_id":       preferred.get("supplier_id"),
        "preferred_supplier_name":     preferred.get("supplier_name"),
        "preferred_supplier_reliable": float(preferred.get("taux_fiabilite") or 0) >= 0.90,
        "preferred_supplier_active":   bool(preferred.get("supplier_actif")),
        "supplier_order_multiple":     int(preferred.get("commande_multiple") or 1),
        "fallback_supplier_name":      fallback.get("supplier_name") if fallback else None,
        "fallback_supplier_active":    bool(fallback.get("supplier_actif")) if fallback else False,
    }


def get_product_info(sku) -> Dict[str, Any]:
    """Retourne les infos produit depuis produits + product_master + fournisseurs."""
    sku = int(sku)

    row = _query("""
        SELECT p.sku, p.nom AS product_name, p.categorie AS category,
               p.famille AS family, p.prix_ht, p.prix_ttc AS unit_price,
               p.marge_pct, p.actif AS active,
               p.flag_4g, p.flag_5g, p.marque AS brand,
               pm.unit_cost, pm.lead_time_days, pm.lead_time_std,
               pm.moq, pm.holding_cost_pct, pm.order_cost,
               pm.lifecycle_stage
        FROM sales.produits p
        LEFT JOIN inventory.products pm ON pm.sku = p.sku
        WHERE p.sku = %s
    """, (sku,), fetch="one")

    if row:
        info = {
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
            "flag_4g": bool(row.get("flag_4g")),
            "flag_5g": bool(row.get("flag_5g")),
            "brand": row.get("brand") or "",
            "source": "postgresql",
        }
        info.update(_get_supplier_context(sku))
        return info

    logger.warning("No product data for %s", sku)
    fallback_info = {
        "sku": sku, "product_name": f"SKU {sku}", "category": "",
        "unit_cost": 0, "unit_price": 0, "lead_time_days": 10,
        "moq": 1, "lifecycle_stage": "unknown", "source": "none",
        "flag_4g": False, "flag_5g": False, "brand": "",
    }
    fallback_info.update(_get_supplier_context(sku))
    return fallback_info


# ══════════════════════════════════════════════════════════════════════════════
# 3. SALES HISTORY — Historique ventes (pour series temporelles)
# ══════════════════════════════════════════════════════════════════════════════

def get_sales_history(sku, store_id: str = DEFAULT_STORE_ID, days: int = 365) -> pd.DataFrame:
    """
    Retourne l'historique des ventes depuis inventory.sales_history.
    Compatible TimesFM/Prophet : colonnes date, quantity_sold, revenue, is_promo.

    FIX: le SELECT filtrait par sku/store_id (WHERE) mais ne les renvoyait pas
    en colonnes. extract_series_from_sales() (appele par fetch_node) cherche
    une colonne sku/product_id/article_id comme TOUTE PREMIERE etape et,
    a defaut, retourne immediatement [0.0]*7 -- silencieusement, sans erreur.
    Consequence live avant ce fix : le moteur TS "primaire" tournait sur une
    serie de 7 zeros pour CHAQUE SKU, en permanence. Ajoute sku/store_id en
    colonnes constantes (valeurs deja connues, un seul sku/store par appel).
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
        df["sku"] = sku
        df["store_id"] = store_id
        return df
    except Exception as e:
        logger.warning("Sales history error for %s@%s: %s", sku, store_id, e)
        return pd.DataFrame(columns=["date", "quantity_sold", "revenue", "is_promo", "sku", "store_id"])
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 4. STOCK HISTORY — Historique niveaux de stock
# ══════════════════════════════════════════════════════════════════════════════

def get_stock_history(sku, store_id: str = DEFAULT_STORE_ID, days: int = 365) -> pd.DataFrame:
    """
    Retourne l'historique stock depuis inventory.stock_history.
    Compatible series temporelles : colonnes date, stock_level, is_stockout.

    FIX: meme bug que get_sales_history — sku/store_id filtrent le WHERE mais
    n'etaient pas renvoyes en colonnes. Ajoutes en constantes.
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
        df["sku"] = sku
        df["store_id"] = store_id
        return df
    except Exception as e:
        logger.warning("Stock history error for %s@%s: %s", sku, store_id, e)
        return pd.DataFrame(columns=["date", "stock_level", "is_stockout", "sku", "store_id"])
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 5. FORECAST DATA — Donnees pour prediction (sales + stock combinees)
# ══════════════════════════════════════════════════════════════════════════════

def get_forecast_data(sku, store_id: str = DEFAULT_STORE_ID, days: int = 30) -> pd.DataFrame:
    """
    Lit le forecast persiste depuis inventory.demand_forecast.

    AVANT (v1): joignait sales_history + stock_history et renommait
    quantity_sold -> predicted_demand — c'etait des ACTUELS relabelles, pas une
    vraie prevision. Corrige ici pour lire la table demand_forecast, alimentee
    par le pipeline demand-sensing (run_baseline_batch.py / run_sensing_job.py).
    Voir implementation guide Section 2 / Step 3.

    Note: `days` etait un parametre "fenetre passee" avant (defaut 180). Ici
    c'est une fenetre future (defaut 30, aligne avec l'usage forecast) —
    verifier les appelants qui passaient un `days` explicite pour une fenetre
    passee avant de deployer.
    """
    sku = int(sku)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT forecast_date AS date,
                       COALESCE(corrected_demand, baseline_demand) AS predicted_demand,
                       baseline_demand, corrected_demand, correction_method
                FROM inventory.demand_forecast
                WHERE sku = %s AND store_id = %s AND forecast_date >= CURRENT_DATE
                ORDER BY forecast_date LIMIT %s
            """, (sku, store_id, days))
            cols = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        logger.warning("Forecast data error for %s@%s: %s", sku, store_id, e)
        return pd.DataFrame(columns=["date", "predicted_demand", "baseline_demand", "corrected_demand", "correction_method"])
    finally:
        _release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# 6. ALL SKUs FOR STORE — Liste des SKUs avec stock pour un store
# ══════════════════════════════════════════════════════════════════════════════

def get_all_skus_for_store(store_id: str = DEFAULT_STORE_ID) -> List[int]:
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
# 9b. STORE CAPACITY CONTEXT — type_boutique + total stock units (cached 5min)
# ══════════════════════════════════════════════════════════════════════════════
# Called once per SKU by the analysis agent's fetch_node, but the value is the
# same for every SKU in a given store within a batch run — cache it in-process
# (same TTL pattern as _DataCache below) instead of hitting Postgres per SKU.

_store_ctx_cache: Dict[str, Any] = {}
_store_ctx_ts: Dict[str, float] = {}
_STORE_CTX_TTL: float = 300.0  # 5 minutes


def _store_ctx_fresh(key: str) -> bool:
    return key in _store_ctx_cache and (_time.time() - _store_ctx_ts.get(key, 0)) < _STORE_CTX_TTL


def get_store_type(store_id: str) -> str:
    """type_boutique depuis sales.boutiques — single SELECT, no join."""
    key = f"type:{store_id}"
    if _store_ctx_fresh(key):
        return _store_ctx_cache[key]
    row = _query(
        "SELECT type_boutique FROM sales.boutiques WHERE store_id = %s",
        (store_id,), fetch="one",
    )
    val = (str(row.get("type_boutique") or "S")).strip() if row else "S"
    _store_ctx_cache[key] = val
    _store_ctx_ts[key] = _time.time()
    return val


def get_store_total_stock_units(store_id: str) -> float:
    """SUM(quantity) tous SKUs pour ce store, depuis inventory.stock_levels —
    single aggregate SELECT, no join."""
    key = f"totalstock:{store_id}"
    if _store_ctx_fresh(key):
        return _store_ctx_cache[key]
    row = _query(
        "SELECT SUM(quantity) AS total FROM inventory.stock_levels WHERE store_id = %s",
        (store_id,), fetch="one",
    )
    val = float(row.get("total") or 0) if row else 0.0
    _store_ctx_cache[key] = val
    _store_ctx_ts[key] = _time.time()
    return val


# ══════════════════════════════════════════════════════════════════════════════
# COMPATIBILITY SHIM — _DataCache for routes.py
# ══════════════════════════════════════════════════════════════════════════════
# routes.py importe `from app.inventory.tools.internal.stock_tools import _DataCache`
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
        from app.inventory.pg_data_loader import load_stock_history
        df = load_stock_history()
        cls._cache["stock"] = df; cls._ts["stock"] = _time.time()
        return df

    @classmethod
    def sales(cls) -> pd.DataFrame:
        if cls._fresh("sales"):
            return cls._cache["sales"]
        from app.inventory.pg_data_loader import load_sales_history
        df = load_sales_history()
        cls._cache["sales"] = df; cls._ts["sales"] = _time.time()
        return df

    @classmethod
    def product(cls) -> pd.DataFrame:
        if cls._fresh("product"):
            return cls._cache["product"]
        from app.inventory.pg_data_loader import load_product_master
        df = load_product_master()
        cls._cache["product"] = df; cls._ts["product"] = _time.time()
        return df

    @classmethod
    def forecast(cls) -> pd.DataFrame:
        """
        FIX: this used to load sales_history and relabel quantity_sold ->
        predicted_demand -- i.e. actuals passed off as a forecast, the same
        bug get_forecast_data() in this file was already fixed for (see its
        docstring). Reads inventory.demand_forecast directly instead, same
        COALESCE(corrected_demand, baseline_demand, demand_24h) priority as
        get_forecast_data()/InventoryRepo.get_forecast_range() use, so
        _quick_risk() in routes.py sees the demand-sensing corrected value
        when one exists instead of a stale sales relabel.
        """
        if cls._fresh("forecast"):
            return cls._cache["forecast"]
        cols = ["sku", "store_id", "date", "predicted_demand",
                "baseline_demand", "corrected_demand", "correction_method"]
        try:
            rows = _query("""
                SELECT sku, store_id, forecast_date AS date,
                       COALESCE(corrected_demand, baseline_demand, demand_24h) AS predicted_demand,
                       baseline_demand, corrected_demand, correction_method
                FROM inventory.demand_forecast
                WHERE forecast_date >= CURRENT_DATE
            """)
            df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
            if not df.empty:
                df["sku"] = df["sku"].astype(str)
        except Exception as e:
            logger.warning("[_DataCache.forecast] query error: %s", e)
            df = pd.DataFrame(columns=cols)
        cls._cache["forecast"] = df; cls._ts["forecast"] = _time.time()
        return df

    @classmethod
    def record_sale(cls, sku: str, store_id: str, units: int) -> None:
        """
        Decrement the in-memory stock override for one SKU/store after a
        real-time sale, so the next `_to_inventory_item()` read (which checks
        `_stock_overrides` first) reflects the sale immediately instead of
        waiting for the next batch pipeline cycle.
        """
        key = (str(sku), store_id)
        current = cls._stock_overrides.get(key)
        if current is None:
            try:
                from app.inventory.repositories.inventory_repo import SyncInventoryRepo
                row = SyncInventoryRepo.get_stock_level(str(sku), store_id)
                current = float(row.get("stock_current") or 0) if row else 0.0
            except Exception:
                current = 0.0
        cls._stock_overrides[key] = max(0.0, current - units)

    @classmethod
    def invalidate(cls):
        cls._stock_overrides.clear()
        cls._cache.clear()
        cls._ts.clear()


# ══════════════════════════════════════════════════════════════════════════════
# COMPATIBILITY ALIASES — pour analysis/nodes.py, context/tools.py
# ══════════════════════════════════════════════════════════════════════════════

def get_stock_status(sku, store_id: str = DEFAULT_STORE_ID) -> Dict[str, Any]:
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


def get_forecast(sku, store_id: str = DEFAULT_STORE_ID) -> pd.DataFrame:
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