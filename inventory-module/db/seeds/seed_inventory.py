"""
seed_inventory.py
Seeds the three tables that come from CSVs:
  - inv.stores      ← union of store_id/store_name/region from
                      sales_history.csv AND stock_history.csv
                      (both files reference stores; we must seed ALL of them
                       before init_stock_levels.py runs its FK inserts)
  - inv.products    ← from product_master.csv
  - inv.promotions  ← from promotions.csv

sales_history.csv and stock_history.csv are NOT loaded into DB tables.
They stay as CSV files and are read directly by init_stock_levels.py
to initialize inv.stock_levels (live state).

CSV schemas
-----------
product_master.csv : sku, product_name, category, unit_cost, unit_price,
                     lead_time_days, lead_time_std, moq, holding_cost_pct,
                     order_cost, lifecycle_stage
sales_history.csv  : date, store_id, store_name, region, sku, product_name,
                     category, quantity_sold, revenue, unit_price,
                     is_promo, event_name, event_type, season
stock_history.csv  : date, store_id, store_name, region, sku, product_name,
                     category, stock_level, is_stockout
promotions.csv     : promo_id, promo_name, start_date, end_date, sku,
                     product_name, category, discount_pct, promo_type, scope

Run from inventory-module/:
    python db/seeds/seed_inventory.py
"""
import asyncio
import logging
import os
import sys

from pathlib import Path

import pandas as pd
import asyncpg
from dotenv import load_dotenv
MODULE_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(MODULE_ROOT.parent / ".env")

PROCESSED = MODULE_ROOT / "data" / "processed"

PRODUCT_FILE    = PROCESSED / "product_master.csv"
SALES_FILE      = PROCESSED / "sales_history.csv"
STOCK_FILE      = PROCESSED / "stock_history.csv"   # also carries store columns
PROMOTIONS_FILE = PROCESSED / "promotions.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host     = os.getenv("DOCKER_DB_HOST",     "localhost"),
        port     = int(os.getenv("DOCKER_DB_PORT", "5433")),
        database = os.getenv("DOCKER_DB_NAME",     "asc_db"),
        user     = os.getenv("DOCKER_DB_USER",     "asc_user"),
        password = os.getenv("DOCKER_DB_PASSWORD", "asc_password"),
        min_size = 2,
        max_size = 5,
    )


def _load_stores_from_csv(path: Path) -> pd.DataFrame:
    """
    Read store columns (store_id, store_name, region) from a denormalised CSV.
    Both sales_history.csv and stock_history.csv carry these three columns.
    Returns one row per unique store_id.
    """
    df = pd.read_csv(
        path,
        usecols=["store_id", "store_name", "region"],
        dtype={"store_id": str},
        low_memory=False,
    )
    return (
        df[["store_id", "store_name", "region"]]
        .drop_duplicates(subset=["store_id"])
        .dropna(subset=["store_id"])
    )


async def seed_stores(conn: asyncpg.Connection) -> int:
    """
    Stores appear as denormalised columns in BOTH sales_history.csv and
    stock_history.csv. We union both sources so that every store_id
    referenced by either file has a row in inv.stores before the
    FK-constrained tables (stock_levels, etc.) are populated.

    Root cause of the original FK violation:
      stock_history.csv contained store_ids (e.g. 'C01') that were absent
      from sales_history.csv, so they were never inserted into inv.stores,
      and init_stock_levels.py crashed on the foreign-key check.
    """
    logger.info("    Loading stores from sales_history.csv ...")
    sales_stores = _load_stores_from_csv(SALES_FILE)

    logger.info("    Loading stores from stock_history.csv ...")
    stock_stores = _load_stores_from_csv(STOCK_FILE)

    # Union — prefer sales_history values when a store_id appears in both
    # (concat keeps insertion order; drop_duplicates keeps the first hit).
    combined = (
        pd.concat([sales_stores, stock_stores], ignore_index=True)
        .drop_duplicates(subset=["store_id"])
        .dropna(subset=["store_id"])
    )

    only_in_stock = set(stock_stores["store_id"]) - set(sales_stores["store_id"])
    if only_in_stock:
        logger.info(
            f"    ⚠  {len(only_in_stock)} store(s) only in stock_history "
            f"(would have caused FK violation): {sorted(only_in_stock)}"
        )

    count = 0
    for _, r in combined.iterrows():
        sid = str(r["store_id"]).strip()
        if not sid or sid.lower() == "nan":
            continue

        store_name = str(r.get("store_name", sid)).strip()
        region     = str(r["region"]).strip() if pd.notna(r.get("region")) else None

        await conn.execute(
            """
            INSERT INTO inv.stores (store_id, store_name, region, active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (store_id) DO UPDATE SET
                store_name = EXCLUDED.store_name,
                region     = EXCLUDED.region,
                updated_at = NOW()
            """,
            sid, store_name, region,
        )
        count += 1

    logger.info(f"  Stores: {count} rows (union of sales_history + stock_history)")
    return count


async def seed_products(conn: asyncpg.Connection) -> int:
    """
    Source: product_master.csv
    Columns: sku, product_name, category, unit_cost, unit_price,
             lead_time_days, lead_time_std, moq, holding_cost_pct,
             order_cost, lifecycle_stage
    """
    df = pd.read_csv(PRODUCT_FILE, dtype={"sku": str})
    count = 0

    for _, r in df.iterrows():
        sku = str(r.get("sku", "")).strip()
        if not sku or sku.lower() == "nan":
            continue

        def f(col):
            v = r.get(col)
            return float(v) if pd.notna(v) else None

        def i(col):
            v = r.get(col)
            return int(float(v)) if pd.notna(v) else None

        stage = str(r.get("lifecycle_stage", "")).strip().lower()
        if stage not in ("growth", "mature", "decline", "discontinued"):
            stage = None

        await conn.execute(
            """
            INSERT INTO inv.products
                (sku, product_name, category, unit_cost, unit_price,
                 lead_time_days, lead_time_std, moq, holding_cost_pct,
                 order_cost, lifecycle_stage)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (sku) DO UPDATE SET
                product_name     = EXCLUDED.product_name,
                category         = EXCLUDED.category,
                unit_cost        = EXCLUDED.unit_cost,
                unit_price       = EXCLUDED.unit_price,
                lead_time_days   = EXCLUDED.lead_time_days,
                lead_time_std    = EXCLUDED.lead_time_std,
                moq              = EXCLUDED.moq,
                holding_cost_pct = EXCLUDED.holding_cost_pct,
                order_cost       = EXCLUDED.order_cost,
                lifecycle_stage  = EXCLUDED.lifecycle_stage,
                updated_at       = NOW()
            """,
            sku,
            str(r.get("product_name", sku)),
            str(r["category"])   if pd.notna(r.get("category"))  else None,
            f("unit_cost"),      f("unit_price"),
            i("lead_time_days"), f("lead_time_std"),
            i("moq"),            f("holding_cost_pct"),
            f("order_cost"),     stage,
        )
        count += 1

    logger.info(f"  Products: {count} rows")
    return count


async def seed_promotions(conn: asyncpg.Connection) -> int:
    """
    Source: promotions.csv
    Columns: promo_id, promo_name, start_date, end_date, sku,
             product_name, category, discount_pct, promo_type, scope

    product_name is in the CSV but already lives in inv.products — not stored here.
    sku may be null/absent when the promo applies to a whole category.
    """
    valid_products = {
        r["sku"] for r in await conn.fetch("SELECT sku FROM inv.products")
    }

    df = pd.read_csv(
        PROMOTIONS_FILE,
        parse_dates=["start_date", "end_date"],
        dtype={"promo_id": str, "sku": str},
    )
    count = 0
    today = pd.Timestamp.now().date()

    for _, r in df.iterrows():
        promo_id = str(r.get("promo_id", "")).strip()
        if not promo_id or promo_id.lower() == "nan":
            continue

        sku_raw = str(r.get("sku", "")).strip()
        sku = (
            sku_raw
            if (sku_raw and sku_raw.lower() != "nan" and sku_raw in valid_products)
            else None
        )

        scope_raw = str(r.get("scope", "")).strip().lower()
        scope = scope_raw if scope_raw in ("national", "regional", "store") else None

        end_date  = r["end_date"].date()
        is_active = r["start_date"].date() <= today <= end_date

        await conn.execute(
            """
            INSERT INTO inv.promotions
                (promo_id, promo_name, promo_type, start_date, end_date,
                 sku, category, discount_pct, scope, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (promo_id) DO UPDATE SET
                promo_name   = EXCLUDED.promo_name,
                end_date     = EXCLUDED.end_date,
                discount_pct = EXCLUDED.discount_pct,
                is_active    = EXCLUDED.is_active
            """,
            promo_id,
            str(r.get("promo_name", promo_id)),
            str(r["promo_type"])     if pd.notna(r.get("promo_type"))   else None,
            r["start_date"].date(),
            end_date,
            sku,
            str(r["category"])       if pd.notna(r.get("category"))     else None,
            float(r["discount_pct"]) if pd.notna(r.get("discount_pct")) else None,
            scope,
            is_active,
        )
        count += 1

    logger.info(f"  Promotions: {count} rows")
    return count


async def main():
    logger.info("=" * 55)
    logger.info("Inventory seed — stores, products, promotions")
    logger.info("=" * 55)

    missing = [
        f for f in [PRODUCT_FILE, SALES_FILE, STOCK_FILE, PROMOTIONS_FILE]
        if not f.exists()
    ]
    if missing:
        for f in missing:
            logger.error(f"Missing: {f}")
        sys.exit(1)

    pool = await get_pool()
    async with pool.acquire() as conn:
        logger.info("Seeding stores (sales_history.csv ∪ stock_history.csv)...")
        await seed_stores(conn)

        logger.info("Seeding products (product_master.csv)...")
        await seed_products(conn)

        logger.info("Seeding promotions (promotions.csv)...")
        await seed_promotions(conn)

    await pool.close()
    logger.info("=" * 55)
    logger.info("Done. Next: python db/seeds/init_stock_levels.py")
    logger.info("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())