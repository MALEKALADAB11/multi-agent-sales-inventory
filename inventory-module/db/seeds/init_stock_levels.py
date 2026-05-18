"""
init_stock_levels.py
Initializes inv.stock_levels (live state) from historical CSV data.

This runs ONCE after seed_inventory.py. It answers:
  stock_current           → most recent stock_level from stock_history.csv
  stock_min               → reorder point = avg_daily_demand × lead_time_days
  stock_max               → max(moq × 3, avg_daily_demand × (lead_time + 7))
  remaining_days_of_stock → stock_current / avg_daily_demand

CSV schemas consumed
--------------------
sales_history.csv : date, store_id, store_name, region, sku, product_name,
                    category, quantity_sold, revenue, unit_price,
                    is_promo, event_name, event_type, season
stock_history.csv : date, store_id, store_name, region, sku, product_name,
                    category, stock_level, is_stockout

Run from inventory-module/:
    python db/seeds/init_stock_levels.py

Safe to re-run (ON CONFLICT DO UPDATE) to recalibrate thresholds.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import asyncpg
from dotenv import load_dotenv

MODULE_ROOT = Path(__file__).parent.parent.parent
load_dotenv(MODULE_ROOT / ".env")

PROCESSED  = MODULE_ROOT / "data" / "processed"
SALES_FILE = PROCESSED / "sales_history.csv"
STOCK_FILE = PROCESSED / "stock_history.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host     = os.getenv("DB_HOST",     "localhost"),
        port     = int(os.getenv("DB_PORT", "5432")),
        database = os.getenv("DB_NAME",     "asc_db"),
        user     = os.getenv("DB_USER",     "asc_user"),
        password = os.getenv("DB_PASSWORD", "asc_password"),
        min_size = 2,
        max_size = 5,
    )


def compute_demand_stats(sales_df: pd.DataFrame) -> dict:
    """
    Returns avg_daily_demand per (sku, store_id).
    Uses the last 90 days of data if available, otherwise all rows.

    sales_history.csv columns used: date, sku, store_id, quantity_sold
    """
    sales_df["date"] = pd.to_datetime(sales_df["date"])
    cutoff = sales_df["date"].max() - pd.Timedelta(days=90)
    recent = sales_df[sales_df["date"] >= cutoff]

    stats = (
        recent.groupby(["sku", "store_id"])["quantity_sold"]
        .agg(["sum", "count"])
        .reset_index()
    )
    stats["avg_daily_demand"] = stats["sum"] / stats["count"]
    stats["avg_daily_demand"] = stats["avg_daily_demand"].clip(lower=0.01)

    return {
        (r["sku"], r["store_id"]): r["avg_daily_demand"]
        for _, r in stats.iterrows()
    }


def compute_latest_stock(stock_df: pd.DataFrame) -> dict:
    """
    Returns the most recent stock_level per (sku, store_id).

    stock_history.csv columns used: date, sku, store_id, stock_level
    """
    stock_df["date"] = pd.to_datetime(stock_df["date"])
    latest = (
        stock_df.sort_values("date")
        .groupby(["sku", "store_id"])
        .last()
        .reset_index()
    )
    return {
        (r["sku"], r["store_id"]): int(r["stock_level"])
        for _, r in latest.iterrows()
    }


async def init_stock_levels(conn: asyncpg.Connection, pool: asyncpg.Pool) -> int:
    """
    For each (sku, store_id) pair present in stock_history.csv:
      - stock_current      = most recent historical stock_level
      - stock_min          = avg_daily_demand × lead_time_days  (reorder point)
      - stock_max          = max(moq × 3, avg_daily_demand × (lead_time + 7))
      - remaining_days     = stock_current / avg_daily_demand
      - stock_in_transit   = 0  (unknown; updated when reorders are approved)

    Rows whose store_id or sku is not present in inv.stores / inv.products
    are skipped with a warning (seed_inventory.py should have seeded both).
    """
    logger.info("Reading CSV files...")
    sales_df = pd.read_csv(
        SALES_FILE,
        dtype={"store_id": str, "sku": str},
        low_memory=False,
    )
    stock_df = pd.read_csv(
        STOCK_FILE,
        dtype={"store_id": str, "sku": str},
        low_memory=False,
    )

    demand_stats  = compute_demand_stats(sales_df)
    latest_stocks = compute_latest_stock(stock_df)

    # Fetch valid FKs from DB (already seeded by seed_inventory.py)
    products = await conn.fetch(
        "SELECT sku, lead_time_days, moq FROM inv.products"
    )
    product_info = {
        r["sku"]: {
            "lead_time_days": r["lead_time_days"] or 7,
            "moq":            r["moq"] or 10,
        }
        for r in products
    }

    valid_store_ids = {
        r["store_id"]
        for r in await conn.fetch("SELECT store_id FROM inv.stores")
    }

    count            = 0
    skipped_sku      = 0
    skipped_store    = 0
    unknown_stores   = set()

    for (sku, store_id), stock_current in latest_stocks.items():

        # Guard FK: store_id must exist in inv.stores
        if store_id not in valid_store_ids:
            unknown_stores.add(store_id)
            skipped_store += 1
            continue

        # Guard FK: sku must exist in inv.products
        if sku not in product_info:
            skipped_sku += 1
            continue

        info       = product_info[sku]
        lead_time  = info["lead_time_days"]
        moq        = info["moq"]
        avg_demand = demand_stats.get((sku, store_id), 1.0)

        # Reorder point: cover the supplier lead time
        stock_min = max(1, round(avg_demand * lead_time))

        # Upper bound: one replenishment cycle (lead time + 7-day review)
        stock_max = max(moq * 3, round(avg_demand * (lead_time + 7)))

        remaining = round(stock_current / avg_demand, 1) if avg_demand > 0 else None

        await conn.execute(
            """
            INSERT INTO inv.stock_levels
                (sku, store_id, stock_current, stock_in_transit,
                 stock_min, stock_max, remaining_days_of_stock, last_updated)
            VALUES ($1, $2, $3, 0, $4, $5, $6, NOW())
            ON CONFLICT (sku, store_id) DO UPDATE SET
                stock_current           = EXCLUDED.stock_current,
                stock_min               = EXCLUDED.stock_min,
                stock_max               = EXCLUDED.stock_max,
                remaining_days_of_stock = EXCLUDED.remaining_days_of_stock,
                last_updated            = NOW()
            """,
            sku, store_id, stock_current, stock_min, stock_max, remaining,
        )
        count += 1

    logger.info(
        f"  stock_levels: {count} rows initialized | "
        f"{skipped_sku} skipped (unknown sku) | "
        f"{skipped_store} skipped (unknown store)"
    )

    if unknown_stores:
        logger.warning(
            f"  ⚠  These store_ids were in stock_history.csv but missing from "
            f"inv.stores — re-run seed_inventory.py to fix: {sorted(unknown_stores)}"
        )

    # Surface items that are already below their reorder point
    low_stock = await conn.fetch(
        """
        SELECT sku, store_id, stock_current, stock_min, remaining_days_of_stock
        FROM inv.stock_levels
        WHERE stock_current <= stock_min
        ORDER BY remaining_days_of_stock ASC NULLS FIRST
        LIMIT 10
        """
    )
    if low_stock:
        logger.info(f"\n  ⚠  {len(low_stock)} items already below stock_min:")
        for r in low_stock:
            logger.info(
                f"    {r['sku']} @ {r['store_id']}: "
                f"current={r['stock_current']} min={r['stock_min']} "
                f"days_left={r['remaining_days_of_stock']}"
            )

    return count


async def main():
    logger.info("=" * 55)
    logger.info("Initializing inv.stock_levels from historical data")
    logger.info("=" * 55)

    for f in [SALES_FILE, STOCK_FILE]:
        if not f.exists():
            logger.error(f"Missing: {f}")
            sys.exit(1)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await init_stock_levels(conn, pool)

    await pool.close()
    logger.info("Done. Next: run seed_static_data.py")


if __name__ == "__main__":
    asyncio.run(main())