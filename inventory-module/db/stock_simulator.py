"""
stock_simulator.py
Keeps inventory.stock_levels up to date as things happen in the system.

This is NOT a random simulator — it's a thin layer that your agents
call when real events occur (a sale is recorded, a reorder is approved,
stock is received, etc.). It updates stock_current and recalculates
remaining_days_of_stock automatically.

In development, replay_sales_as_today() drives stock changes from the
historical sales data as if they were happening now.

Usage from an agent:
    from db.stock_simulator import StockSimulator
    sim = StockSimulator(repo)

    await sim.record_sale(sku="SKU001", store_id="S01", quantity=2)
    await sim.record_reorder_approved(sku="SKU001", store_id="S01", quantity=50)
    await sim.record_reception(sku="SKU001", store_id="S01", quantity=50)

Design note on record_sale / record_adjustment:
    These methods ONLY update existing rows. They never INSERT new rows.
    Seeding inventory.stock_levels is the job of init_stock_levels.py.
    Attempting to INSERT here would trigger FK violations on inventory.stores
    for any store_id not present in that table.
"""
import logging
import os
from datetime import date, timedelta
from typing import Optional

import psycopg2

from db.repositories.inventory_repo import InventoryRepo

logger = logging.getLogger(__name__)


def _pg_conn():
    """Open a direct psycopg2 connection for synchronous demand queries."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
        port=int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
        dbname=os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
        user=os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "admin")),
        connect_timeout=10,
    )


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

        If no stock_levels row exists for (sku, store_id), logs a warning
        and returns {} without attempting an INSERT — seeding is the init
        script's job and an INSERT here would cause FK violations on inventory.stores.
        """
        current = await self.repo.get_stock_level(sku, store_id)
        if current is None:
            logger.warning(
                "[Simulator] No stock_levels row for %s@%s — skipping sale. "
                "Run init_stock_levels.py to seed this (sku, store_id) pair.",
                sku, store_id,
            )
            return {}

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
            "[Simulator] Sale: %s@%s %d → %d units (qty=%d, days_left=%s)",
            sku, store_id, current["stock_current"], new_stock, quantity, remaining,
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
            logger.warning("[Simulator] No stock_levels row for %s@%s", sku, store_id)
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
            "[Simulator] Reorder approved: %s@%s in_transit=%d (added %d)",
            sku, store_id, new_in_transit, quantity,
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
            logger.warning("[Simulator] No stock_levels row for %s@%s", sku, store_id)
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
            "[Simulator] Reception: %s@%s stock %d → %d | transit %d → %d",
            sku, store_id, current["stock_current"], new_stock, in_transit, new_transit,
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

        If no stock_levels row exists for (sku, store_id), logs a warning
        and returns {} — same policy as record_sale: no INSERT from here.
        """
        current = await self.repo.get_stock_level(sku, store_id)
        if current is None:
            logger.warning(
                "[Simulator] No stock_levels row for %s@%s — skipping adjustment (%s). "
                "Run init_stock_levels.py to seed this pair.",
                sku, store_id, reason,
            )
            return {}

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

        logger.info("[Simulator] Adjustment: %s@%s → %d (%s)", sku, store_id, new_stock, reason)
        return await self.repo.get_stock_level(sku, store_id)

    # ── Dev tool: replay sales history to drive stock down ───────────────────

    async def replay_sales_as_today(
        self,
        store_id:  str,
        days_back: int = 7,
        speed:     float = 1.0,
    ) -> int:
        """
        Development tool. Takes recent rows from inventory.sales_history and
        applies them as if they are happening now, driving stock_current down.

        speed: demand multiplier — 1.0 = real rate, 2.0 = twice as fast.
        Returns: number of SKU sale events applied.
        """
        try:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT sku, SUM(quantity_sold) AS total_sold
                        FROM inventory.sales_history
                        WHERE store_id = %s
                          AND record_date >= CURRENT_DATE - %s * INTERVAL '1 day'
                        GROUP BY sku
                    """, (store_id, days_back))
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[Simulator] replay_sales_as_today DB error: %s", exc)
            return 0

        applied = 0
        for row in rows:
            sku        = str(row[0])
            total_sold = int(float(row[1] or 0) * speed)
            if total_sold <= 0:
                continue

            current = await self.repo.get_stock_level(sku, store_id)
            if current is None:
                continue

            await self.record_sale(sku, store_id, total_sold)
            applied += 1

        logger.info(
            "[Simulator] Replayed %dd of sales for %s: %d SKUs updated",
            days_back, store_id, applied,
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
        Computes average daily demand from inventory.sales_history (PostgreSQL).
        Falls back to 1.0 if no history exists for this (sku, store_id).
        """
        try:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT SUM(quantity_sold), COUNT(DISTINCT record_date)
                        FROM inventory.sales_history
                        WHERE sku = %s AND store_id = %s
                          AND record_date >= CURRENT_DATE - %s * INTERVAL '1 day'
                    """, (str(sku), store_id, days_back))
                    row = cur.fetchone()
                    if row and row[1] and int(row[1]) > 0:
                        return max(float(row[0] or 0) / int(row[1]), 0.01)
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[Simulator] _get_avg_daily_demand DB error: %s", exc)
        return 1.0

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
