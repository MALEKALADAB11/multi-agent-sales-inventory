"""
Stock Tools — Shared Data Layer
================================
Responsibilities:
  - Lazy-load CSVs once per process (_DataCache)
  - DB-first, CSV-fallback data reads
  - Live stock depletion tracking (record_sale)

NO metric computation — that lives in src/agents/analysis/tools.py.

Public API (same readable names callers expect):
  get_stock_status(sku, store_id)   -> dict   (replaces the old string version)
  get_product(sku)                  -> dict | None
  get_sales_history(sku, store_id)  -> DataFrame
  get_forecast(sku, store_id)       -> DataFrame
  get_store(store_id)               -> dict | None
"""

import pandas as pd
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

import sys
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from config.settings import (
    STOCK_HISTORY_PATH,
    SALES_HISTORY_PATH,
    PRODUCT_MASTER_PATH,
    FORECAST_OUTPUT_PATH,
    DEFAULT_STORE,
)

try:
    from db.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — DB reads disabled, using CSVs only.")


# ═══════════════════════════════════════════════════════════════════════════
# Data Cache — Lazy-loaded CSV storage
# ═══════════════════════════════════════════════════════════════════════════

class _DataCache:
    """
    Lazy-loaded CSV cache. Reads each file at most once per process.
    Thread-safe for read operations (GIL protects initial load).
    """

    _stock_df: Optional[pd.DataFrame] = None
    _product_df: Optional[pd.DataFrame] = None
    _sales_df: Optional[pd.DataFrame] = None
    _forecast_df: Optional[pd.DataFrame] = None

    # In-memory stock overrides: (sku, store_id) -> current units on hand.
    # Populated by record_sale() for live depletion tracking.
    _stock_overrides: Dict[tuple, float] = {}

    @classmethod
    def stock(cls) -> pd.DataFrame:
        if cls._stock_df is None:
            cls._stock_df = pd.read_csv(STOCK_HISTORY_PATH, parse_dates=["date"])
            cls._stock_df["sku"] = cls._stock_df["sku"].astype(str)
            if "store_id" in cls._stock_df.columns:
                cls._stock_df["store_id"] = cls._stock_df["store_id"].astype(str)
            logger.debug("Loaded stock_history.csv (%d rows)", len(cls._stock_df))
        return cls._stock_df

    @classmethod
    def product(cls) -> pd.DataFrame:
        if cls._product_df is None:
            cls._product_df = pd.read_csv(PRODUCT_MASTER_PATH)
            cls._product_df["sku"] = cls._product_df["sku"].astype(str)
            logger.debug("Loaded product_master.csv (%d rows)", len(cls._product_df))
        return cls._product_df

    @classmethod
    def sales(cls) -> pd.DataFrame:
        if cls._sales_df is None:
            cls._sales_df = pd.read_csv(
                SALES_HISTORY_PATH,
                parse_dates=["date"],
                low_memory=False,  # évite DtypeWarning colonnes mixtes
            )
            cls._sales_df = cls._sales_df.copy()
            cls._sales_df["sku"] = cls._sales_df["sku"].astype(str)
            if "store_id" in cls._sales_df.columns:
                cls._sales_df["store_id"] = cls._sales_df["store_id"].astype(str)
            logger.debug("Loaded sales_history.csv (%d rows)", len(cls._sales_df))
        return cls._sales_df

    @classmethod
    def forecast(cls) -> pd.DataFrame:
        if cls._forecast_df is None:
            cls._forecast_df = pd.read_csv(
                str(FORECAST_OUTPUT_PATH),
                parse_dates=["date"],
                low_memory=False,  # évite DtypeWarning et tuple index out of range
            )
            # Reconstruire le block manager après chargement mixed-type
            cls._forecast_df = cls._forecast_df.copy()
            if "sku" in cls._forecast_df.columns:
                cls._forecast_df["sku"] = cls._forecast_df["sku"].astype(str)
            if "store_id" in cls._forecast_df.columns:
                cls._forecast_df["store_id"] = cls._forecast_df["store_id"].astype(str)
            logger.debug("Loaded forecast CSV (%d rows)", len(cls._forecast_df))
        return cls._forecast_df

    @classmethod
    def record_sale(cls, sku: str, store_id: str, qty: float) -> None:
        """
        Decrement in-memory stock when a sale occurs.
        Called by the sales simulator for live depletion tracking.
        Thread-safe: GIL protects dict writes at this granularity.
        """
        sku = str(sku)
        store_id = str(store_id)
        key = (sku, store_id)

        if key not in cls._stock_overrides:
            df = cls.stock()
            rows = df[(df["sku"] == sku) & (df["store_id"] == store_id)]
            seed = float(rows.sort_values("date").iloc[-1]["stock_level"]) if not rows.empty else 0.0
            cls._stock_overrides[key] = seed

        cls._stock_overrides[key] = max(0.0, cls._stock_overrides[key] - qty)
        logger.debug(
            "Sale recorded: %s@%s −%.0f units → %.0f remaining",
            sku, store_id, qty, cls._stock_overrides[key],
        )

    @classmethod
    def invalidate(cls) -> None:
        """Clear cache. Call if CSVs change on disk."""
        cls._stock_df = None
        cls._product_df = None
        cls._sales_df = None
        cls._forecast_df = None
        cls._stock_overrides = {}
        logger.info("Data cache invalidated")


# ═══════════════════════════════════════════════════════════════════════════
# Public Data Access Functions
# ═══════════════════════════════════════════════════════════════════════════

def get_stock_status(sku: str, store_id: str = DEFAULT_STORE) -> Dict[str, Any]:
    """
    Get current stock data for a SKU at a store.
    DB first, CSV fallback. Applies in-memory sale overrides.

    Returns:
        {
            "stock_current": float,
            "stock_in_transit": float,
            "stock_min": float | None,   # manager-set threshold
            "stock_max": float | None,   # manager-set threshold
            "source": "db" | "csv" | "none"
        }
    """
    sku = str(sku)
    store_id = str(store_id)

    if _DB_AVAILABLE:
        try:
            db_stock = SyncInventoryRepo.get_stock_level(sku=sku, store_id=store_id)
            if db_stock:
                override_key = (sku, store_id)
                stock_current = float(db_stock.get("stock_current", 0))
                if override_key in _DataCache._stock_overrides:
                    stock_current = _DataCache._stock_overrides[override_key]
                return {
                    "stock_current":    stock_current,
                    "stock_in_transit": float(db_stock.get("stock_in_transit", 0)),
                    "stock_min":        db_stock.get("stock_min"),
                    "stock_max":        db_stock.get("stock_max"),
                    "source":           "db",
                }
        except Exception as e:
            logger.warning("DB stock read failed for %s@%s: %s", sku, store_id, e)

    # CSV fallback
    df = _DataCache.stock()
    rows = df[(df["sku"] == sku) & (df["store_id"] == store_id)]

    if rows.empty:
        logger.warning("No stock data for %s@%s in DB or CSV", sku, store_id)
        return {
            "stock_current": 0.0, "stock_in_transit": 0.0,
            "stock_min": None, "stock_max": None, "source": "none",
        }

    override_key = (sku, store_id)
    stock_current = float(rows.sort_values("date").iloc[-1]["stock_level"])
    if override_key in _DataCache._stock_overrides:
        stock_current = _DataCache._stock_overrides[override_key]

    return {
        "stock_current":    stock_current,
        "stock_in_transit": 0.0,   # CSV doesn't track in-transit
        "stock_min":        None,  # CSV doesn't have manager thresholds
        "stock_max":        None,
        "source":           "csv",
    }


def get_product(sku: str) -> Optional[Dict[str, Any]]:
    """
    Get product master data for a SKU.
    DB first, CSV fallback.

    Returns dict with all product fields, or None if not found.
    """
    sku = str(sku)

    if _DB_AVAILABLE:
        try:
            db_product = SyncInventoryRepo.get_product(sku=sku)
            if db_product:
                return db_product
        except Exception as e:
            logger.warning("DB product read failed for %s: %s", sku, e)

    df = _DataCache.product()
    rows = df[df["sku"] == sku]

    if rows.empty:
        logger.warning("No product data for %s in DB or CSV", sku)
        return None

    return rows.iloc[0].to_dict()


def get_sales_history(sku: str, store_id: str) -> pd.DataFrame:
    """
    Get sales history for a SKU at a store.
    CSV only — no DB equivalent.

    Returns filtered DataFrame with all sales_history.csv columns.
    """
    sku = str(sku)
    store_id = str(store_id)

    df = _DataCache.sales()
    filtered = df[(df["sku"] == sku) & (df["store_id"] == store_id)].copy()

    if filtered.empty:
        logger.debug("No sales history for %s@%s", sku, store_id)

    return filtered


def get_forecast(sku: str, store_id: str) -> pd.DataFrame:
    """
    Get demand forecast for a SKU at a store.
    CSV only — no DB equivalent.

    Returns filtered DataFrame with date, predicted_demand columns.

    Note: .reset_index(drop=True) est requis après le filtre boolean pour
    reconstruire le block manager pandas et éviter le IndexError 'tuple index
    out of range' causé par des colonnes mixed-type dans le CSV source.
    """
    sku = str(sku)
    store_id = str(store_id)

    try:
        df = _DataCache.forecast().copy()

        if "sku" in df.columns:
            df = df[df["sku"] == sku].reset_index(drop=True)
        if "store_id" in df.columns:
            df = df[df["store_id"] == store_id].reset_index(drop=True)

        if df.empty:
            logger.debug("No forecast for %s@%s", sku, store_id)

        return df

    except Exception as e:
        logger.warning("get_forecast error for %s@%s: %s — returning empty", sku, store_id, e)
        return pd.DataFrame(columns=["date", "predicted_demand", "sku", "store_id"])


def get_store(store_id: str) -> Optional[Dict[str, Any]]:
    """
    Get store information.
    DB only — no CSV fallback needed.

    Returns dict with store_id, store_name, region, active, or None.
    """
    store_id = str(store_id)

    if _DB_AVAILABLE:
        try:
            return SyncInventoryRepo.get_store(store_id=store_id)
        except Exception as e:
            logger.warning("DB store read failed for %s: %s", store_id, e)

    return None