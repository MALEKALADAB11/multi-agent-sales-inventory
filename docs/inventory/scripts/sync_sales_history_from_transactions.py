"""Daily sync: sales.transactions (live, per-transaction) -> inventory.sales_history
(daily aggregate, what run_baseline_batch.py / run_sensing_job.py actually read).

Why this exists
----------------
inventory.sales_history was seeded once from a 3-year CSV (2023-01-01 to
2026-03-01) so the baseline model can learn seasonality -- it was never
meant to be kept live by that seed. sales.transactions is the live,
per-sale table that keeps growing day to day. This script bridges the two:
it rolls sales.transactions up into the daily grain sales_history expects,
so the forecasting pipeline's "last 7 days" features (recent_actual_avg,
stockout_flag_7d, etc. -- see sensing_features.py) have real, current data
to read instead of hitting the dead zone after 2026-03-01.

Where this goes in the daily schedule
--------------------------------------
Run this FIRST, before run_baseline_batch.py and run_sensing_job.py --
both depend on sales_history being current.

Idempotency
-----------
Safe to rerun / safe to miss a day and catch up later. Each run fully
REPLACES whatever's in sales_history for the (store, day) combinations it
just recomputed from sales.transactions, rather than appending -- so
reruns never create duplicates, and a late/missed run just gets caught up
by SYNC_WINDOW_DAYS being wider than 1 day. This only ever touches the
recent sync window; the historical CSV-seeded rows (2023-2026-03) are
never in range and are never touched.
"""
import sys, asyncio, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
from app.inventory.tools.internal.stock_tools import _query
from app.inventory.repositories.inventory_repo import InventoryRepo

# Default for the daily scheduled run. Wider than 1 day on purpose -- if a
# scheduled run gets skipped (outage, holiday, etc.) the next run still
# catches up every day it missed, since each run recomputes and replaces
# the whole window, not just "yesterday".
#
# For a one-time WIDER backfill (e.g. closing the gap between
# sales_history's old CSV cutoff and where sales.transactions picks up),
# override with --days on the command line instead of editing this. Then
# go back to running it with no arguments (this default) for the daily job.
SYNC_WINDOW_DAYS = 10


async def main(days: int):
    print(f"Pulling + aggregating last {days} days from "
          f"sales.transactions...", flush=True)

    # Aggregate at the DB level, not in pandas -- sales.transactions is
    # one row per individual sale, inventory.sales_history is one row per
    # (sku, store, day). This GROUP BY/SUM is exactly the grain change
    # that bridges the two.
    agg_rows = _query(f"""
        SELECT sku, store_id, date_only AS record_date,
               SUM(quantity) AS quantity_sold,
               SUM(lig_ttc) AS revenue,
               ROUND(AVG(prix_unitaire)::numeric, 2) AS unit_price
        FROM sales.transactions
        WHERE date_only >= CURRENT_DATE - INTERVAL '{days} days'
        GROUP BY sku, store_id, date_only
    """)

    if not agg_rows:
        print("No recent rows in sales.transactions -- nothing to sync.", flush=True)
        return

    agg_df = pd.DataFrame(agg_rows)
    dates = sorted(agg_df["record_date"].unique())
    store_ids = sorted(agg_df["store_id"].unique())
    print(f"  {len(agg_df)} (sku, store, day) rows across {len(dates)} days, "
          f"{len(store_ids)} stores: {store_ids}", flush=True)

    insert_rows = [
        (
            int(r.sku), r.store_id, r.record_date,
            int(r.quantity_sold),
            float(r.revenue) if pd.notna(r.revenue) else None,
            float(r.unit_price) if pd.notna(r.unit_price) else None,
        )
        for r in agg_df.itertuples(index=False)
    ]

    repo = InventoryRepo()
    await repo.connect()
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            # Replace-in-place, scoped to (record_date, store_id) -- not
            # sku -- because each run aggregates ALL skus sold in that
            # store/day. A sku-scoped upsert would leave stale rows behind
            # for any sku that sold on a previous run but not this one
            # (e.g. it stopped selling, or a return zeroed it out).
            deleted = await conn.execute("""
                DELETE FROM inventory.sales_history
                WHERE record_date = ANY($1::date[])
                  AND store_id = ANY($2::text[])
            """, list(dates), list(store_ids))
            print(f"  cleared existing sync-window rows ({deleted})", flush=True)

            # store_name/region/product_name/category/season/etc. are left
            # NULL on synced rows -- run_sensing_job.py's feature pipeline
            # (sensing_features.py) only ever reads sku, store_id,
            # record_date, quantity_sold off sales_history; everything
            # else it needs (category, promo, events) comes from separate
            # tables passed in alongside it. If some other consumer of
            # sales_history later needs those descriptive columns filled
            # in too, extend this INSERT with joins to inventory.products /
            # sales.boutiques at that point.
            await conn.executemany("""
                INSERT INTO inventory.sales_history
                    (sku, store_id, record_date, quantity_sold, revenue, unit_price)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, insert_rows)

    await repo.close()
    print(f"Synced {len(insert_rows)} rows into inventory.sales_history.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--days", type=int, default=SYNC_WINDOW_DAYS,
        help=(
            "How many days back (from today) to re-aggregate from "
            "sales.transactions into sales_history. Default is the normal "
            f"daily window ({SYNC_WINDOW_DAYS}). Pass a larger value once "
            "for a one-time backfill of a gap, e.g. --days 140 to cover "
            "2026-03-02 through today."
        ),
    )
    args = parser.parse_args()
    asyncio.run(main(args.days))
