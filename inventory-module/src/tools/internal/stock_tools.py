"""
MCP Inventory Tools  (optimised)
=================================
Same public API as before. Key change: CSVs are loaded ONCE at module import
time and cached as module-level DataFrames.  Every tool function filters the
in-memory frames instead of hitting the disk on every call.

Why this matters
----------------
The original code called pd.read_csv() 2–3 times per tool function, and
there are 3 tool functions called per SKU, so a single SKU analysis caused
6–9 disk reads of the same files.  With module-level caching that drops to
one read per file for the entire process lifetime.

Nothing else changed: formulas, risk thresholds, service-level logic, and
return formats are identical so downstream agents are unaffected.
"""

import math
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Any, Tuple
import sys
import logging

logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from config.settings import (
    STOCK_HISTORY_PATH,
    SALES_HISTORY_PATH,
    PRODUCT_MASTER_PATH,
    PROMOTIONS_PATH,
    FORECAST_OUTPUT_PATH,
    DEFAULT_STORE,
    BUSINESS_OBJECTIVE_SETTINGS,
)


# ---------------------------------------------------------------------------
# Module-level cache — loaded once, reused forever
# ---------------------------------------------------------------------------

class _DataCache:
    """Lazy-loaded CSV cache. Reads each file at most once per process."""

    _stock_df: pd.DataFrame | None = None
    _product_df: pd.DataFrame | None = None
    _sales_df: pd.DataFrame | None = None
    _forecast_df: pd.DataFrame | None = None

    # In-memory stock overrides: (sku, store_id) -> current units on hand.
    # Populated by record_sale() as the sales simulator fires events.
    # This avoids re-reading the CSV (which never updates at runtime)
    # while still reflecting live sales depletion.
    _stock_overrides: dict = {}

    @classmethod
    def stock(cls) -> pd.DataFrame:
        if cls._stock_df is None:
            cls._stock_df = pd.read_csv(STOCK_HISTORY_PATH, parse_dates=["date"])
            # FIX: cast sku/store_id to str so string lookups always match
            cls._stock_df["sku"] = cls._stock_df["sku"].astype(str)
            if "store_id" in cls._stock_df.columns:
                cls._stock_df["store_id"] = cls._stock_df["store_id"].astype(str)
            logger.debug("Loaded stock_history CSV (%d rows)", len(cls._stock_df))
        return cls._stock_df

    @classmethod
    def product(cls) -> pd.DataFrame:
        if cls._product_df is None:
            cls._product_df = pd.read_csv(PRODUCT_MASTER_PATH)
            # FIX: cast sku to str so string lookups always match
            cls._product_df["sku"] = cls._product_df["sku"].astype(str)
            logger.debug("Loaded product_master CSV (%d rows)", len(cls._product_df))
        return cls._product_df

    @classmethod
    def sales(cls) -> pd.DataFrame:
        if cls._sales_df is None:
            cls._sales_df = pd.read_csv(SALES_HISTORY_PATH, parse_dates=["date"])
            # FIX: cast sku/store_id to str so string lookups always match
            cls._sales_df["sku"] = cls._sales_df["sku"].astype(str)
            if "store_id" in cls._sales_df.columns:
                cls._sales_df["store_id"] = cls._sales_df["store_id"].astype(str)
            logger.debug("Loaded sales_history CSV (%d rows)", len(cls._sales_df))
        return cls._sales_df

    @classmethod
    def forecast(cls) -> pd.DataFrame:
        if cls._forecast_df is None:
            cls._forecast_df = pd.read_csv(
                str(FORECAST_OUTPUT_PATH), parse_dates=["date"]
            )
            logger.debug("Loaded forecast CSV (%d rows)", len(cls._forecast_df))
        return cls._forecast_df

    @classmethod
    def record_sale(cls, store_id: str, sku: str, qty: float) -> None:
        """
        Decrement in-memory stock when the sales simulator fires a sale event.
        Called by the inventory sale hook in main.py on every simulator tick.
        Thread-safe: GIL protects dict writes at this granularity.
        """
        # FIX: normalise to str so the key always matches the CSV-loaded str skus
        sku      = str(sku)
        store_id = str(store_id)
        key = (sku, store_id)
        if key not in cls._stock_overrides:
            # Seed from the latest CSV row for this SKU so we start accurate
            df = cls.stock()
            rows = df[(df["sku"] == sku) & (df["store_id"] == store_id)]
            if not rows.empty:
                seed = float(rows.sort_values("date").iloc[-1]["stock_level"])
            else:
                seed = 0.0
            cls._stock_overrides[key] = seed
        cls._stock_overrides[key] = max(0.0, cls._stock_overrides[key] - qty)
        logger.debug(
            "Sale recorded: %s@%s  −%.0f units  → %.0f remaining",
            sku, store_id, qty, cls._stock_overrides[key],
        )

    @classmethod
    def get_current_stock(cls, sku: str, store_id: str) -> float:
        """
        Returns the live stock level for a SKU.
        Uses the in-memory override if sales have been recorded since startup,
        otherwise falls back to the last row of the stock_history CSV.
        """
        # FIX: normalise to str so the key always matches
        sku      = str(sku)
        store_id = str(store_id)
        key = (sku, store_id)
        if key in cls._stock_overrides:
            return cls._stock_overrides[key]
        df = cls.stock()
        rows = df[(df["sku"] == sku) & (df["store_id"] == store_id)]
        if rows.empty:
            return 0.0
        return float(rows.sort_values("date").iloc[-1]["stock_level"])

    @classmethod
    def invalidate(cls) -> None:
        """Call this if the underlying CSVs have changed and you need fresh data."""
        cls._stock_df = None
        cls._product_df = None
        cls._sales_df = None
        cls._forecast_df = None
        cls._stock_overrides = {}   # also reset live stock counters


# ---------------------------------------------------------------------------
# Z-score lookup
# ---------------------------------------------------------------------------

def _service_level_to_z(service_level: float) -> float:
    """Convert a service level (fill rate) to a Z-score via scipy or lookup."""
    try:
        from scipy.stats import norm
        return float(norm.ppf(service_level))
    except ImportError:
        table = {
            0.75: 0.674, 0.80: 0.842, 0.85: 1.036,
            0.90: 1.282, 0.92: 1.405, 0.93: 1.476,
            0.95: 1.645, 0.96: 1.751, 0.97: 1.881,
            0.98: 2.054, 0.99: 2.326, 0.995: 2.576,
        }
        nearest = min(table.keys(), key=lambda k: abs(k - service_level))
        return table[nearest]


# ---------------------------------------------------------------------------
# Effective service level
# ---------------------------------------------------------------------------

def _effective_service_level(
    product_sl: float,
    lifecycle_stage: str,
    business_objective: str,
) -> Tuple[float, str]:
    """
    Returns (effective_service_level, explanation_string).
    Applies lifecycle adjustment then clips to business-objective bounds.
    """
    sl = product_sl
    notes = []

    lifecycle_adjustments = {
        "growth":     +0.02,
        "new_launch": +0.03,
        "mature":      0.00,
        "decline":    -0.05,
        "phase_out":  -0.10,
    }
    adj = lifecycle_adjustments.get(lifecycle_stage.lower().replace(" ", "_"), 0.0)
    if adj != 0.0:
        sl += adj
        direction = "boosted" if adj > 0 else "reduced"
        notes.append(f"lifecycle={lifecycle_stage} ({direction} by {abs(adj):.0%})")

    obj_settings = {
        "cost":          {"min": 0.75,              "max": product_sl},
        "balanced":      {"min": product_sl,         "max": product_sl},
        "service_level": {"min": max(product_sl, 0.98), "max": 0.999},
        "competitive":   {"min": max(product_sl, 0.95), "max": 0.999},
    }
    bounds = obj_settings.get(business_objective, {"min": product_sl, "max": product_sl})
    sl_clipped = max(bounds["min"], min(sl, bounds["max"]))
    if sl_clipped != sl:
        notes.append(
            f"objective={business_objective} clipped {sl:.2%}→{sl_clipped:.2%}"
        )
    sl = sl_clipped
    sl = max(0.70, min(sl, 0.999))

    explanation = (
        f"Base={product_sl:.0%}, adjusted to {sl:.0%}"
        + (f" ({'; '.join(notes)})" if notes else " (no adjustments)")
    )
    return sl, explanation


# ---------------------------------------------------------------------------
# Demand std dev — uses cached sales frame
# ---------------------------------------------------------------------------

def _demand_std(
    sku: str,
    store_id: str,
    avg_daily: float,
    lookback_days: int = 90,
) -> float:
    """
    Compute daily demand std dev from the cached sales_history frame.
    Falls back to 30 % of mean if history is insufficient.
    """
    try:
        rows = _DataCache.sales()
        rows = rows[
            (rows["sku"] == str(sku)) & (rows["store_id"] == str(store_id))
        ].copy()

        if len(rows) < 14:
            return avg_daily * 0.30

        rows = rows.sort_values("date").tail(lookback_days)
        daily = rows.groupby("date")["quantity_sold"].sum()
        std = float(daily.std())
        return std if not math.isnan(std) else avg_daily * 0.30

    except Exception:
        return avg_daily * 0.30


# ---------------------------------------------------------------------------
# Public tool functions (same signatures + return formats as before)
# ---------------------------------------------------------------------------

def get_stock_status(sku: str, store_id: str = DEFAULT_STORE) -> str:
    """
    Returns current stock level, lead time (avg ± std), MOQ,
    costs, lifecycle stage, and service level target for a given SKU.
    Uses the cached DataFrames — no disk I/O on repeated calls.
    """
    sku      = str(sku)
    store_id = str(store_id)

    stock_df   = _DataCache.stock()
    product_df = _DataCache.product()

    stock_rows = stock_df[
        (stock_df["sku"] == sku) & (stock_df["store_id"] == store_id)
    ]
    if stock_rows.empty:
        return f"No stock data found for SKU '{sku}' at store '{store_id}'."

    stock_row = stock_rows.sort_values("date").iloc[-1]

    prod_rows = product_df[product_df["sku"] == sku]
    if prod_rows.empty:
        return f"No product master data found for SKU '{sku}'."
    p = prod_rows.iloc[0]

    return (
        f"=== Stock Status: {sku} @ {store_id} ===\n"
        f"  Product          : {p.get('product_name', 'N/A')}\n"
        f"  Category         : {p.get('category', 'N/A')}\n"
        f"  Lifecycle stage  : {p.get('lifecycle_stage', 'N/A')}\n"
        f"  Stock level      : {int(_DataCache.get_current_stock(sku, store_id))} units\n"
        f"  Stockout flag    : {'YES' if stock_row.get('is_stockout', 0) else 'No'}\n"
        f"  Lead time avg    : {float(p['lead_time_days']):.0f} days\n"
        f"  Lead time std    : {float(p.get('lead_time_std', 0)):.1f} days\n"
        f"  MOQ              : {int(p['moq'])} units\n"
        f"  Unit cost        : {float(p['unit_cost']):.2f} DT\n"
        f"  Unit price       : {float(p['unit_price']):.2f} DT\n"
        f"  Holding cost pct : {float(p.get('holding_cost_pct', 0.25)):.0%} / year\n"
        f"  Order cost       : {float(p.get('order_cost', 0)):.2f} DT / order\n"
        f"  Service lvl tgt  : {float(p.get('service_level_target', 0.95)):.0%}\n"
        f"  Last updated     : {stock_row['date'].date()}"
    )


def get_forecast_summary(sku: str, store_id: str = DEFAULT_STORE) -> str:
    """
    Returns the 30-day demand forecast summary from TimesFM.
    Raw model output — no promotional adjustment applied.
    Uses the cached forecast DataFrame.
    """
    sku      = str(sku)
    store_id = str(store_id)

    df = _DataCache.forecast().copy()

    if "sku" in df.columns:
        df = df[df["sku"].astype(str) == sku]
    if "store_id" in df.columns:
        df = df[df["store_id"].astype(str) == store_id]

    if df.empty:
        return f"No forecast data found for SKU '{sku}' at store '{store_id}'."

    total    = df["predicted_demand"].sum()
    avg      = df["predicted_demand"].mean()
    peak     = df["predicted_demand"].max()
    low      = df["predicted_demand"].min()
    peak_dt  = df.loc[df["predicted_demand"].idxmax(), "date"].date()
    start    = df["date"].min().date()
    end      = df["date"].max().date()
    days     = len(df)

    half            = days // 2
    df_sorted       = df.sort_values("date")
    first_half_avg  = df_sorted.head(half)["predicted_demand"].mean()
    second_half_avg = df_sorted.tail(half)["predicted_demand"].mean()
    if second_half_avg > first_half_avg * 1.05:
        trend = "up"
    elif second_half_avg < first_half_avg * 0.95:
        trend = "down"
    else:
        trend = "stable"

    df_sorted["week"] = (
        df_sorted["date"] - df_sorted["date"].min()
    ).dt.days // 7 + 1
    weekly     = df_sorted.groupby("week")["predicted_demand"].sum().round(1)
    weekly_str = "  |  ".join([f"W{w}: {v:.0f}" for w, v in weekly.items()])

    demand_std = _demand_std(sku, store_id, avg)

    return (
        f"=== Forecast (TimesFM Baseline): {sku} @ {store_id} ===\n"
        f"  Period         : {start} → {end} ({days} days)\n"
        f"  Total demand   : {total:.0f} units\n"
        f"  Daily avg      : {avg:.2f} units/day\n"
        f"  Daily std dev  : {demand_std:.2f} units/day\n"
        f"  Daily peak     : {peak:.2f} units on {peak_dt}\n"
        f"  Daily low      : {low:.2f} units/day\n"
        f"  Trend          : {trend}\n"
        f"  Weekly split   : {weekly_str}\n"
        f"  Note           : No promotional adjustment applied."
    )


def compute_inventory_metrics(
    sku: str,
    store_id: str = DEFAULT_STORE,
    promo_uplift_pct: float = 0.0,
    business_objective: str = "balanced",
) -> str:
    """
    Computes full inventory replenishment metrics (APICS safety stock, EOQ,
    ROP, risk classification, service level).  Uses cached DataFrames.
    """
    sku      = str(sku)
    store_id = str(store_id)

    stock_df    = _DataCache.stock()
    product_df  = _DataCache.product()
    forecast_df = _DataCache.forecast().copy()

    stock_rows = stock_df[
        (stock_df["sku"] == sku) & (stock_df["store_id"] == store_id)
    ]
    if stock_rows.empty:
        return f"No stock data for SKU '{sku}' at store '{store_id}'."
    stock_row = stock_rows.sort_values("date").iloc[-1]

    prod_rows = product_df[product_df["sku"] == sku]
    if prod_rows.empty:
        return f"No product master data for SKU '{sku}'."
    p = prod_rows.iloc[0]

    if "sku" in forecast_df.columns:
        forecast_df = forecast_df[forecast_df["sku"].astype(str) == sku]
    if "store_id" in forecast_df.columns:
        forecast_df = forecast_df[forecast_df["store_id"].astype(str) == store_id]
    if forecast_df.empty:
        return f"No forecast data for SKU '{sku}' at store '{store_id}'."

    # ── Product parameters ─────────────────────────────────────────────────
    # current_stock comes from the live override (updated by sales events)
    # rather than the static CSV, so risk metrics reflect real-time depletion.
    current_stock    = _DataCache.get_current_stock(sku, store_id)
    lead_time_avg    = float(p["lead_time_days"])
    lead_time_std    = float(p.get("lead_time_std", 0))
    moq              = float(p["moq"])
    unit_cost        = float(p["unit_cost"])
    holding_cost_pct = float(p.get("holding_cost_pct", 0.25))
    order_cost       = float(p.get("order_cost", 50.0))
    lifecycle_stage  = str(p.get("lifecycle_stage", "mature"))
    product_sl       = float(p.get("service_level_target", 0.95))

    # ── Demand ─────────────────────────────────────────────────────────────
    avg_daily_base  = float(forecast_df["predicted_demand"].mean())
    total_30_base   = float(forecast_df["predicted_demand"].sum())
    forecast_sorted = forecast_df.sort_values("date")
    total_7_base    = (
        float(forecast_sorted.head(7)["predicted_demand"].sum())
        if len(forecast_sorted) >= 7
        else total_30_base * (7 / 30)
    )

    uplift_factor = 1.0 + (promo_uplift_pct / 100.0)
    avg_daily     = avg_daily_base * uplift_factor
    total_30      = total_30_base  * uplift_factor
    total_7       = total_7_base   * uplift_factor

    demand_std = _demand_std(sku, store_id, avg_daily_base)

    # ── Effective service level ────────────────────────────────────────────
    eff_sl, sl_explanation = _effective_service_level(
        product_sl, lifecycle_stage, business_objective
    )
    Z = _service_level_to_z(eff_sl)

    # ── Safety stock (APICS) ───────────────────────────────────────────────
    variance     = (lead_time_avg * demand_std ** 2) + (avg_daily ** 2 * lead_time_std ** 2)
    safety_stock = Z * math.sqrt(variance)

    # ── Reorder point ──────────────────────────────────────────────────────
    reorder_point = avg_daily * lead_time_avg + safety_stock

    # ── EOQ ────────────────────────────────────────────────────────────────
    annual_demand = avg_daily * 365
    if holding_cost_pct > 0 and unit_cost > 0 and order_cost > 0:
        eoq = math.sqrt(
            (2 * annual_demand * order_cost) / (holding_cost_pct * unit_cost)
        )
    else:
        eoq = moq

    recommended_order_qty = max(moq, round(eoq))

    # ── Days of stock remaining ────────────────────────────────────────────
    days_remaining = current_stock / avg_daily if avg_daily > 0 else 999.0

    # ── Risk classification ────────────────────────────────────────────────
    lt_variability_buffer = lead_time_std * 2

    if days_remaining < lead_time_avg:
        risk = "CRITICAL"
        risk_rationale = (
            f"Stock ({days_remaining:.1f}d) will run out before the next order "
            f"arrives (lead time {lead_time_avg:.0f}d). Stockout is imminent."
        )
    elif days_remaining < lead_time_avg + lt_variability_buffer:
        risk = "HIGH"
        risk_rationale = (
            f"Stock ({days_remaining:.1f}d) is within the lead time variability "
            f"window ({lead_time_avg:.0f}d avg ± {lead_time_std:.1f}d std). "
            f"A delayed shipment would cause a stockout."
        )
    elif days_remaining < lead_time_avg * 2.5:
        risk = "MEDIUM"
        risk_rationale = (
            f"Stock ({days_remaining:.1f}d) is above the variability window "
            f"but below 2.5× lead time ({lead_time_avg * 2.5:.0f}d). "
            f"Monitor and plan replenishment."
        )
    else:
        risk = "LOW"
        risk_rationale = (
            f"Stock ({days_remaining:.1f}d) exceeds 2.5× lead time "
            f"({lead_time_avg * 2.5:.0f}d). Well covered."
        )

    overstock = days_remaining > 90
    if overstock:
        risk_rationale += (
            f" WARNING: {days_remaining:.0f} days of stock — excess capital tied up."
        )

    # ── Cost analysis ──────────────────────────────────────────────────────
    eoq_order_cost    = order_cost
    eoq_holding_cost  = (eoq / 2) * unit_cost * holding_cost_pct
    safety_stock_cost = safety_stock * unit_cost
    total_replen_cost = recommended_order_qty * unit_cost

    uplift_note = (
        f"  Uplift applied            : +{promo_uplift_pct:.0f}% (Context Agent)\n"
        if promo_uplift_pct > 0
        else "  Uplift applied            : none — baseline\n"
    )

    return (
        f"=== Inventory Metrics: {sku} @ {store_id} ===\n\n"

        f"Current Status:\n"
        f"  Stock level               : {current_stock:.0f} units\n"
        f"  Days of stock remaining   : {days_remaining:.1f} days\n"
        f"  Risk level                : {risk}\n"
        f"  Risk rationale            : {risk_rationale}\n"
        f"  Overstock flag            : {'YES' if overstock else 'No'}\n\n"

        f"Demand:\n"
        f"  Base daily avg            : {avg_daily_base:.2f} units/day\n"
        f"  Daily std dev             : {demand_std:.2f} units/day\n"
        f"{uplift_note}"
        f"  Adjusted daily avg        : {avg_daily:.2f} units/day\n"
        f"  30d total (adjusted)      : {total_30:.0f} units\n\n"

        f"Service Level:\n"
        f"  {sl_explanation}\n"
        f"  Z-score                   : {Z:.3f}\n\n"

        f"Safety Stock (APICS formula):\n"
        f"  SS = Z × sqrt(LT × σd² + d² × σLT²)\n"
        f"  = {Z:.3f} × sqrt({lead_time_avg:.0f}×{demand_std:.2f}² + "
        f"{avg_daily:.2f}²×{lead_time_std:.1f}²)\n"
        f"  = {safety_stock:.0f} units\n"
        f"  Safety stock cost         : {safety_stock_cost:,.0f} DT\n\n"

        f"Replenishment:\n"
        f"  Lead time                 : {lead_time_avg:.0f}d avg ± {lead_time_std:.1f}d std\n"
        f"  Reorder point             : {reorder_point:.0f} units\n"
        f"    (= avg_demand × LT_avg + safety_stock)\n"
        f"  EOQ                       : {eoq:.0f} units\n"
        f"    (= sqrt(2 × {annual_demand:.0f} × {order_cost:.0f} / "
        f"({holding_cost_pct:.0%} × {unit_cost:.2f})))\n"
        f"  EOQ holding cost/cycle    : {eoq_holding_cost:,.0f} DT\n"
        f"  MOQ                       : {moq:.0f} units\n"
        f"  Recommended order qty     : {recommended_order_qty:.0f} units\n"
        f"    (= max(MOQ={moq:.0f}, EOQ={eoq:.0f}))\n"
        f"  Order cost (fixed)        : {eoq_order_cost:,.0f} DT\n"
        f"  Total replenishment cost  : {total_replen_cost:,.0f} DT\n"
        f"  Business objective        : {business_objective}\n"
        f"  Lifecycle stage           : {lifecycle_stage}"
    )


# ---------------------------------------------------------------------------
# Tool registry for MCP server
# ---------------------------------------------------------------------------

INVENTORY_TOOLS = {
    "get_stock_status":          {"function": get_stock_status},
    "get_forecast_summary":      {"function": get_forecast_summary},
    "compute_inventory_metrics": {"function": compute_inventory_metrics},
}