"""
seed_history.py
=================
Seeds:
  inventory.sales_history  <- data/processed/sales_history.csv  (aggregated)
  inventory.stock_history  <- data/processed/stock_history.csv

IMPORTANT — sales_history.csv grain
-------------------------------------
sales_history.csv carries columns (agent_id, num_client, heure, type_transaction,
tx_rem) that inventory.sales_history has no room for — those look like
transaction-level fields, not daily-aggregate ones. That's a real signal your
file may be one-row-per-sale rather than one-row-per (date, store, sku), even
though the original script's docstring described it as the latter.

Rather than guess, this script GROUPS BY (record_date, store_id, sku) before
inserting. This is safe either way:
  - if the CSV is already at that grain, grouping is a no-op (1 row stays 1 row)
  - if it's transaction-level, this correctly rolls it up
quantity_sold/revenue are summed; unit_price is recomputed as revenue/qty
(more correct than picking one transaction's price); is_promo/is_event_day
use "any true wins"; everything else (event_name, season, calendar fields)
uses the first value in the group, since those are date-level, not
transaction-level, facts.

If you want the transaction-level detail too (payment method, per-sale
agent/client), that likely belongs in sales.transactions instead — that
table has a matching grain (heure, agent_id, sku, quantity, prix_unitaire)
but sales_history.csv doesn't carry a payment_method column, so it's a
partial fit at best. Flagging rather than forcing it.

Uses conn.copy_records_to_table (bulk COPY) instead of per-row INSERT —
both files are likely large (multi-year daily data).

Run: python db/seeds/seed_history.py
Re-run safe: existing rows deleted first.

Fix (2026-07-09): optional text fields (store_name, region, product_name,
category, event_name, event_type, season, promo_type, event_intensity)
now pass through _seed_common.clean() so a blank CSV cell (pandas NaN)
becomes NULL instead of crashing asyncpg's text codec with "expected str,
got float". copy_records_to_table is stricter about types than a plain
INSERT, so this matters even more here than in the per-row-execute scripts.
"""
import asyncio
import logging
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool, DATA_PROCESSED, clean

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

SALES_COLS = [
    "record_date", "store_id", "store_name", "region", "sku", "product_name",
    "category", "quantity_sold", "revenue", "unit_price", "is_promo",
    "event_name", "event_type", "season", "promo_type", "day_of_week",
    "week_of_year", "month_num", "year_num", "is_weekend", "is_event_day",
    "event_intensite", "uplift_factor",
]

STOCK_COLS = [
    "record_date", "store_id", "store_name", "region", "sku",
    "product_name", "category", "stock_level", "is_stockout",
]


def _build_sales_records(df: pd.DataFrame) -> list[tuple]:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    first_cols = ["store_name", "region", "product_name", "category", "event_name",
                  "event_type", "season", "promo_type", "jour_semaine", "week_of_year",
                  "month_num", "year_num", "is_weekend", "event_intensity", "uplift_factor"]
    agg = {c: "first" for c in first_cols if c in df.columns}
    agg["quantity_sold"] = "sum"
    agg["revenue"] = "sum"
    if "is_promo" in df.columns:
        agg["is_promo"] = "max"
    if "is_event_day" in df.columns:
        agg["is_event_day"] = "max"

    grouped = df.groupby(["date", "store_id", "sku"], as_index=False).agg(agg)
    grouped["unit_price"] = np.where(
        grouped["quantity_sold"] > 0, grouped["revenue"] / grouped["quantity_sold"], 0
    )

    records = []
    for _, r in grouped.iterrows():
        records.append((
            r["date"], str(r["store_id"]), clean(r.get("store_name")), clean(r.get("region")),
            int(r["sku"]), clean(r.get("product_name")), clean(r.get("category")),
            int(r["quantity_sold"]), float(r["revenue"]), float(r["unit_price"]),
            bool(r.get("is_promo", False)), clean(r.get("event_name")), clean(r.get("event_type")),
            clean(r.get("season")), clean(r.get("promo_type")),
            int(r["jour_semaine"]) if pd.notna(r.get("jour_semaine")) else None,
            int(r["week_of_year"]) if pd.notna(r.get("week_of_year")) else None,
            int(r["month_num"]) if pd.notna(r.get("month_num")) else None,
            int(r["year_num"]) if pd.notna(r.get("year_num")) else None,
            bool(r.get("is_weekend", False)), bool(r.get("is_event_day", False)),
            clean(r.get("event_intensity")),
            float(r["uplift_factor"]) if pd.notna(r.get("uplift_factor")) else 1.0,
        ))
    return records


def _build_stock_records(df: pd.DataFrame) -> list[tuple]:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    records = []
    for _, r in df.iterrows():
        records.append((
            r["date"], str(r["store_id"]), clean(r.get("store_name")), clean(r.get("region")),
            int(r["sku"]), clean(r.get("product_name")), clean(r.get("category")),
            int(r["stock_level"]) if pd.notna(r.get("stock_level")) else 0,
            bool(r.get("is_stockout", False)),
        ))
    return records


async def _insert_in_batches(conn, table_name: str, schema_name: str, columns: list[str], records: list[tuple], batch_size: int = 10_000):
    for offset in range(0, len(records), batch_size):
        batch = records[offset:offset + batch_size]
        if batch:
            await conn.copy_records_to_table(table_name, schema_name=schema_name, columns=columns, records=batch)


async def main():
    sales_chunk_iter = pd.read_csv(
        DATA_PROCESSED / "sales_history.csv",
        engine="c",
        low_memory=False,
        on_bad_lines="skip",
        chunksize=100_000,
        encoding="utf-8",
    )
    stock_chunk_iter = pd.read_csv(
        DATA_PROCESSED / "stock_history.csv",
        engine="c",
        low_memory=False,
        on_bad_lines="skip",
        chunksize=100_000,
        encoding="utf-8",
    )

    sales_records = []
    raw_rows = 0
    sales_aggregates = {}
    for chunk in sales_chunk_iter:
        raw_rows += len(chunk)
        chunk = chunk.copy()
        chunk["date"] = pd.to_datetime(chunk["date"]).dt.date
        grouped = chunk.groupby(["date", "store_id", "sku"], as_index=False).agg(
            {
                "store_name": "first",
                "region": "first",
                "product_name": "first",
                "category": "first",
                "quantity_sold": "sum",
                "revenue": "sum",
                "is_promo": "max" if "is_promo" in chunk.columns else "first",
                "event_name": "first",
                "event_type": "first",
                "season": "first",
                "promo_type": "first",
                "jour_semaine": "first",
                "week_of_year": "first",
                "month_num": "first",
                "year_num": "first",
                "is_weekend": "max" if "is_weekend" in chunk.columns else "first",
                "is_event_day": "max" if "is_event_day" in chunk.columns else "first",
                "event_intensity": "first",
                "uplift_factor": "first",
            }
        )
        grouped["unit_price"] = grouped["revenue"] / grouped["quantity_sold"]
        grouped.loc[grouped["quantity_sold"] <= 0, "unit_price"] = 0
        for _, r in grouped.iterrows():
            key = (r["date"], str(r["store_id"]), int(r["sku"]))
            if key not in sales_aggregates:
                sales_aggregates[key] = {
                    "date": r["date"],
                    "store_id": str(r["store_id"]),
                    "store_name": clean(r.get("store_name")),
                    "region": clean(r.get("region")),
                    "sku": int(r["sku"]),
                    "product_name": clean(r.get("product_name")),
                    "category": clean(r.get("category")),
                    "quantity_sold": int(r["quantity_sold"]),
                    "revenue": float(r["revenue"]),
                    "unit_price": float(r["unit_price"]),
                    "is_promo": bool(r.get("is_promo", False)),
                    "event_name": clean(r.get("event_name")),
                    "event_type": clean(r.get("event_type")),
                    "season": clean(r.get("season")),
                    "promo_type": clean(r.get("promo_type")),
                    "jour_semaine": int(r["jour_semaine"]) if pd.notna(r.get("jour_semaine")) else None,
                    "week_of_year": int(r["week_of_year"]) if pd.notna(r.get("week_of_year")) else None,
                    "month_num": int(r["month_num"]) if pd.notna(r.get("month_num")) else None,
                    "year_num": int(r["year_num"]) if pd.notna(r.get("year_num")) else None,
                    "is_weekend": bool(r.get("is_weekend", False)),
                    "is_event_day": bool(r.get("is_event_day", False)),
                    "event_intensity": clean(r.get("event_intensity")),
                    "uplift_factor": float(r["uplift_factor"]) if pd.notna(r.get("uplift_factor")) else 1.0,
                }
            else:
                agg = sales_aggregates[key]
                agg["quantity_sold"] += int(r["quantity_sold"])
                agg["revenue"] += float(r["revenue"])
                agg["unit_price"] = agg["revenue"] / agg["quantity_sold"] if agg["quantity_sold"] else 0.0
                agg["is_promo"] = agg["is_promo"] or bool(r.get("is_promo", False))
                agg["is_weekend"] = agg["is_weekend"] or bool(r.get("is_weekend", False))
                agg["is_event_day"] = agg["is_event_day"] or bool(r.get("is_event_day", False))

    sales_records = [
        (
            agg["date"], agg["store_id"], agg["store_name"], agg["region"], agg["sku"], agg["product_name"], agg["category"],
            agg["quantity_sold"], agg["revenue"], agg["unit_price"], agg["is_promo"], agg["event_name"], agg["event_type"],
            agg["season"], agg["promo_type"], agg["jour_semaine"], agg["week_of_year"], agg["month_num"], agg["year_num"],
            agg["is_weekend"], agg["is_event_day"], agg["event_intensity"], agg["uplift_factor"],
        )
        for agg in sales_aggregates.values()
    ]

    logger.info(f"  sales_history.csv: {raw_rows:,} source rows -> {len(sales_records):,} "
                f"after grouping by (date, store_id, sku)"
                + ("  [no change — source was already at that grain]" if raw_rows == len(sales_records)
                   else "  [source WAS finer-grained — confirms transaction-level rows, now rolled up]"))

    stock_records = []
    for chunk in stock_chunk_iter:
        stock_records.extend(_build_stock_records(chunk))

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM inventory.sales_history")
        await _insert_in_batches(conn, "sales_history", "inventory", SALES_COLS, sales_records)
        await conn.execute("DELETE FROM inventory.stock_history")
        await _insert_in_batches(conn, "stock_history", "inventory", STOCK_COLS, stock_records)
    await pool.close()
    logger.info(f"Done: inventory.sales_history ({len(sales_records):,} rows), "
                f"inventory.stock_history ({len(stock_records):,} rows)")
    logger.info("NOT SEEDED (no destination column in inventory.stock_history): "
                "supplier_id, stock_min, stock_max, stock_in_transit, "
                "days_of_stock_remaining, reorder_triggered")


if __name__ == "__main__":
    asyncio.run(main())
