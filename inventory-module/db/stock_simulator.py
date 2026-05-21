"""
stock_simulator.py
Keeps inv.stock_levels up to date as things happen in the system.

This is NOT a random simulator — it's a thin layer that your agents
call when real events occur (a sale is recorded, a reorder is approved,
stock is received, etc.). It updates stock_current and recalculates
remaining_days_of_stock automatically.

In development, replay_sales_as_today() drives stock changes from the
historical sales CSV as if they were happening now.

NOTE: sales_history and stock_history are CSV files only — they are NOT
DB tables. All historical demand queries in this file read directly from
data/processed/sales_history.csv.

Usage from an agent:
    from db.stock_simulator import StockSimulator
    sim = StockSimulator(repo)

    await sim.record_sale(sku="SKU001", store_id="S01", quantity=2)
    await sim.record_reorder_approved(sku="SKU001", store_id="S01", quantity=50)
    await sim.record_reception(sku="SKU001", store_id="S01", quantity=50)
"""
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from db.repositories.inventory_repo import InventoryRepo

logger = logging.getLogger(__name__)

# Path to the historical sales CSV — read by _get_avg_daily_demand and
# replay_sales_as_today instead of querying a (non-existent) DB table.
_MODULE_ROOT = Path(__file__).parent.parent
_SALES_FILE  = _MODULE_ROOT / "data" / "processed" / "sales_history.csv"

# Module-level cache so we don't re-read the CSV on every demand lookup.
# sales_history.csv columns: date, store_id, store_name, region, sku,
#   product_name, category, quantity_sold, revenue, unit_price,
#   is_promo, event_name, event_type, season
_sales_cache: Optional[pd.DataFrame] = None


def _load_sales_csv() -> pd.DataFrame:
    """
    Lazy-load and cache sales_history.csv.
    Only the columns we actually need are kept in memory.
    """
    global _sales_cache
    if _sales_cache is None:
        logger.debug("Loading sales_history.csv into memory cache...")
        _sales_cache = pd.read_csv(
            _SALES_FILE,
            usecols=["date", "store_id", "sku", "quantity_sold"],
            dtype={"store_id": str, "sku": str},
            parse_dates=["date"],
            low_memory=False,
        )
    return _sales_cache


class StockSimulator:

    def __init__(self, repo: InventoryRepo):
        self.repo = repo

    # ── Core stock mutations ──────────────────────────────────────────────────

    async def record_sale(
        self,
        sku:      str,
        store_id: str,
        quantity: int,
    ) -> dict:
        """
        Called when a sale is recorded for an inventory-tracked product.
        Decreases stock_current, recalculates remaining_days_of_stock.
        Returns the updated stock level dict.
        """
        current = await self.repo.get_stock_level(sku, store_id)
        if current is None:
            logger.warning(
                f"[Simulator] No stock_level row for {sku}@{store_id}, creating one"
            )
            await self.repo.upsert_stock_level(sku, store_id, stock_current=0)
            current = await self.repo.get_stock_level(sku, store_id)

        new_stock  = max(0, current["stock_current"] - quantity)
        avg_demand = await self._get_avg_daily_demand(sku, store_id)
        remaining  = round(new_stock / avg_demand, 1) if avg_demand > 0 else None

        await self.repo.upsert_stock_level(
            sku, store_id,
            stock_current           = new_stock,
            stock_in_transit        = current.get("stock_in_transit", 0),
            stock_min               = current.get("stock_min"),
            stock_max               = current.get("stock_max"),
            remaining_days_of_stock = remaining,
        )

        logger.info(
            f"[Simulator] Sale: {sku}@{store_id} "
            f"{current['stock_current']} → {new_stock} "
            f"(qty={quantity}, days_left={remaining})"
        )
        return await self.repo.get_stock_level(sku, store_id)

    async def record_reorder_approved(
        self,
        sku:      str,
        store_id: str,
        quantity: int,
    ) -> dict:
        """
        Called when a reorder recommendation is approved.
        Increases stock_in_transit — the stock isn't here yet.
        """
        current = await self.repo.get_stock_level(sku, store_id)
        if current is None:
            logger.warning(f"[Simulator] No stock_level row for {sku}@{store_id}")
            return {}

        new_in_transit = (current.get("stock_in_transit") or 0) + quantity

        await self.repo.upsert_stock_level(
            sku, store_id,
            stock_current           = current["stock_current"],
            stock_in_transit        = new_in_transit,
            stock_min               = current.get("stock_min"),
            stock_max               = current.get("stock_max"),
            remaining_days_of_stock = current.get("remaining_days_of_stock"),
        )

        logger.info(
            f"[Simulator] Reorder approved: {sku}@{store_id} "
            f"in_transit={new_in_transit} (added {quantity})"
        )
        return await self.repo.get_stock_level(sku, store_id)

    async def record_reception(
        self,
        sku:      str,
        store_id: str,
        quantity: int,
    ) -> dict:
        """
        Called when ordered stock physically arrives.
        Moves quantity from stock_in_transit to stock_current.
        """
        current = await self.repo.get_stock_level(sku, store_id)
        if current is None:
            logger.warning(f"[Simulator] No stock_level row for {sku}@{store_id}")
            return {}

        in_transit  = current.get("stock_in_transit") or 0
        new_stock   = current["stock_current"] + quantity
        new_transit = max(0, in_transit - quantity)

        avg_demand = await self._get_avg_daily_demand(sku, store_id)
        remaining  = round(new_stock / avg_demand, 1) if avg_demand > 0 else None

        await self.repo.upsert_stock_level(
            sku, store_id,
            stock_current           = new_stock,
            stock_in_transit        = new_transit,
            stock_min               = current.get("stock_min"),
            stock_max               = current.get("stock_max"),
            remaining_days_of_stock = remaining,
        )

        logger.info(
            f"[Simulator] Reception: {sku}@{store_id} "
            f"stock {current['stock_current']} → {new_stock} "
            f"transit {in_transit} → {new_transit}"
        )
        return await self.repo.get_stock_level(sku, store_id)

    async def record_adjustment(
        self,
        sku:       str,
        store_id:  str,
        new_stock: int,
        reason:    str = "manual_adjustment",
    ) -> dict:
        """
        Direct stock correction (inventory count, loss, damage, etc.).
        Sets stock_current to an absolute value.
        """
        current = await self.repo.get_stock_level(sku, store_id)
        if current is None:
            await self.repo.upsert_stock_level(sku, store_id, stock_current=new_stock)
        else:
            avg_demand = await self._get_avg_daily_demand(sku, store_id)
            remaining  = round(new_stock / avg_demand, 1) if avg_demand > 0 else None
            await self.repo.upsert_stock_level(
                sku, store_id,
                stock_current           = new_stock,
                stock_in_transit        = current.get("stock_in_transit", 0),
                stock_min               = current.get("stock_min"),
                stock_max               = current.get("stock_max"),
                remaining_days_of_stock = remaining,
            )

        logger.info(f"[Simulator] Adjustment: {sku}@{store_id} → {new_stock} ({reason})")
        return await self.repo.get_stock_level(sku, store_id)

    # ── Dev tool: replay sales history to drive stock down ───────────────────

    async def replay_sales_as_today(
        self,
        store_id:  str,
        days_back: int = 7,
        speed:     float = 1.0,
    ) -> int:
        """
        Development tool. Takes recent rows from sales_history.csv and
        applies them as if they are happening now, driving stock_current down.

        Reads directly from data/processed/sales_history.csv — this is a
        CSV file, NOT a DB table.

        speed: demand multiplier — 1.0 = real rate, 2.0 = twice as fast.
        Returns: number of SKU sale events applied.
        """
        sales_df = _load_sales_csv()

        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days_back)
        recent = sales_df[
            (sales_df["store_id"] == store_id) &
            (sales_df["date"] >= cutoff)
        ]

        # Aggregate total sold per SKU over the window
        totals = (
            recent.groupby("sku")["quantity_sold"]
            .sum()
            .reset_index()
            .rename(columns={"quantity_sold": "total_sold"})
        )

        applied = 0
        for _, row in totals.iterrows():
            sku        = row["sku"]
            total_sold = int(row["total_sold"] * speed)
            if total_sold <= 0:
                continue

            current = await self.repo.get_stock_level(sku, store_id)
            if current is None:
                continue

            await self.record_sale(sku, store_id, total_sold)
            applied += 1

        logger.info(
            f"[Simulator] Replayed {days_back}d of sales for {store_id}: "
            f"{applied} SKUs updated"
        )
        return applied

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def schedule_reception(
        self,
        sku:            str,
        store_id:       str,
        quantity:       int,
        lead_time_days: float,
        time_scale:     float = 1.0,
    ) -> None:
        """
        Spawns a background task that waits for the lead time then calls
        record_reception() — moving stock from stock_in_transit to stock_current.

        Lead time is randomised ±20% to simulate real-world supplier variability.

        time_scale: compresses time for demo/dev.
          1.0    = real time (lead_time_days = actual days)
          1/24   = 1 day becomes 1 hour
          1/1440 = 1 day becomes 1 minute  ← good for demo

        Usage after approving a reorder:
            await sim.record_reorder_approved(sku, store_id, qty)
            await sim.schedule_reception(sku, store_id, qty,
                                         lead_time_days=10,
                                         time_scale=1/1440)
        """
        import asyncio as _asyncio
        import random as _random

        async def _deliver():
            noise        = _random.uniform(0.8, 1.2)
            wait_seconds = lead_time_days * noise * time_scale * 86400
            arrival_days = round(lead_time_days * noise, 1)

            logger.info(
                "[Simulator] Reorder scheduled: %s@%s qty=%d | "
                "expected in %.1fd (±20%% noise) | demo wait=%.0fs",
                sku, store_id, quantity, arrival_days, wait_seconds,
            )
            await _asyncio.sleep(wait_seconds)
            logger.info(
                "[Simulator] Stock arriving: %s@%s qty=%d (after %.1fd)",
                sku, store_id, quantity, arrival_days,
            )
            await self.record_reception(sku, store_id, quantity)

        _asyncio.create_task(_deliver())

    async def _get_avg_daily_demand(
        self,
        sku:      str,
        store_id: str,
        days_back: int = 30,
    ) -> float:
        """
        Computes average daily demand from sales_history.csv.
        Reads the CSV (cached after first load) — NOT a DB table.
        Falls back to 1.0 if no history exists for this (sku, store_id).

        sales_history.csv columns used: date, store_id, sku, quantity_sold
        """
        sales_df = _load_sales_csv()

        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days_back)
        subset = sales_df[
            (sales_df["sku"] == sku) &
            (sales_df["store_id"] == store_id) &
            (sales_df["date"] >= cutoff)
        ]

        if subset.empty:
            return 1.0

        total_sold  = subset["quantity_sold"].sum()
        unique_days = subset["date"].nunique()

        avg = float(total_sold) / unique_days if unique_days > 0 else 1.0
        return max(avg, 0.01)

    async def get_stock_status(self, store_id: str) -> dict:
        """
        Returns a summary of stock health for a store.
        Useful for the analysis_agent to decide where to focus.
        """
        items = await self.repo.get_store_stock_levels(store_id)

        critical   = [i for i in items if i["stock_current"] == 0]
        low        = [i for i in items if 0 < i["stock_current"] <= (i.get("stock_min") or 0)]
        in_transit = [i for i in items if (i.get("stock_in_transit") or 0) > 0]

        return {
            "store_id":          store_id,
            "total_skus":        len(items),
            "stockouts":         len(critical),
            "low_stock":         len(low),
            "in_transit_orders": len(in_transit),
            "critical_items": [
                {
                    "sku":          i["sku"],
                    "product_name": i.get("product_name"),
                    "stock":        i["stock_current"],
                    "days_left":    i.get("remaining_days_of_stock"),
                }
                for i in critical[:10]
            ],
        }