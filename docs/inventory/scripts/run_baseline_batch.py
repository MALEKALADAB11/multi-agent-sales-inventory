import torch
import coreforecast.exponentially_weighted  # noqa: F401
# ^ MUST be imported before pandas/numpy — Windows DLL load-order conflict
#   between coreforecast's bundled native runtime and numpy's bundled
#   OpenBLAS. If pandas/numpy load first, importing coreforecast (pulled
#   in transitively via timeseries_engine -> statsforecast) crashes the
#   process with exit code -1073741819 (0xC0000005, access violation) and
#   no Python traceback. Confirmed via `python -X faulthandler`.
#
# IMPORTANT for the parallel version below: ProcessPoolExecutor workers on
# Windows are freshly spawned interpreters — they do NOT inherit this
# process's sys.modules cache. Every worker re-imports this module from
# scratch and would hit the exact same DLL crash independently unless the
# import order is fixed at THIS file's own top level too (same fix already
# applied in backfill.py for the same reason).

import os
import sys, asyncio
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
from app.inventory.forecasting.timeseries_engine import forecast as ts_forecast, extract_series_from_sales
from app.inventory.tools.internal.stock_tools import _query
from app.inventory.repositories.inventory_repo import InventoryRepo

# --- TEMPORARY: run on a small sample first to confirm the whole pipeline
# works end-to-end before committing to the full 4258-pair run. Same
# pattern already used in backfill_baseline_forecasts.py's TOP_N_PAIRS.
# Set to None for the real full run (do that once this has been validated).
#
# 500 is a middle ground: far more coverage than the earlier 200-pair
# smoke test, but nowhere near the full ~4262-pair run that was taking
# hours. Combined with MUST_INCLUDE_STORES below so this stays USEFUL
# right now, not just faster.
TOP_N_PAIRS = 500

# Pairs are normally chosen by sales volume (top_pairs, below) -- but the
# stores synced live via sync_sales_history_from_transactions.py (I14,
# M15, M18, M22) are low-volume relative to the long-history CSU-era
# stores, so a plain top-500-by-volume cut would exclude them entirely --
# exactly the bug that caused run_sensing_job.py to correct 0 rows last
# time. These stores are explicitly guaranteed a slot regardless of
# volume ranking, since run_sensing_job.py has nothing to correct for a
# pair unless a baseline forecast exists for it.
MUST_INCLUDE_STORES = {"I14", "M15", "M18", "M22"}


def _process_one_pair(args):
    """Runs in a worker process — must be a top-level function so it can be
    pickled and sent to the worker, same pattern as backfill.py's
    _process_one_pair. Pure computation only, no DB access here.
    """
    sku, store_id, group_df = args
    series = extract_series_from_sales(group_df, sku, store_id, days_back=730)
    if len(series) < 90:
        return []
    result = ts_forecast(series, horizon=30)
    today = pd.Timestamp.now().date()
    return [
        {
            "sku": sku, "store_id": store_id,
            "forecast_date": today + pd.Timedelta(days=i + 1),
            "baseline_demand": float(val), "model_version": "baseline_mstl_v1",
        }
        for i, val in enumerate(result["forecast_values"])
    ]


async def main():
    print("Querying sales history (up to 730 days -- this can take a while "
          "with no output before it, that's expected, not frozen)...", flush=True)
    sales_df = pd.DataFrame(_query(
        "SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '730 days'"
    ))
    print(f"  loaded {len(sales_df)} sales rows", flush=True)
    # extract_series_from_sales() only recognizes a date column named
    # "date" / "transaction_date" / "ds" — a raw SELECT * gives "record_date",
    # which it silently doesn't match (falls back to an all-zero series
    # rather than raising). Alias it so every SKU doesn't quietly get a
    # flat-zero baseline.
    sales_df["date"] = pd.to_datetime(sales_df["record_date"])

    pairs_list = list(sales_df[["sku", "store_id"]].drop_duplicates().itertuples(index=False, name=None))

    if TOP_N_PAIRS is not None:
        volume_by_pair = (
            sales_df.groupby(["sku", "store_id"])["quantity_sold"].sum()
            .sort_values(ascending=False)
        )
        top_pairs = set(volume_by_pair.head(TOP_N_PAIRS).index)
        must_include_pairs = {
            (sku, store_id) for sku, store_id in pairs_list if store_id in MUST_INCLUDE_STORES
        }
        keep_pairs = top_pairs | must_include_pairs
        pairs_list = [p for p in pairs_list if p in keep_pairs]
        print(f"TOP_N_PAIRS={TOP_N_PAIRS} set — running on the top {len(top_pairs)} "
              f"pairs by volume plus {len(must_include_pairs)} guaranteed pairs from "
              f"MUST_INCLUDE_STORES ({sorted(MUST_INCLUDE_STORES)}) — {len(pairs_list)} "
              f"pairs total. Set TOP_N_PAIRS=None for the real full run.", flush=True)

    print(f"Forecasting {len(pairs_list)} sku/store pairs (parallel)...", flush=True)

    # Pre-group once so each worker gets only the rows it needs, instead of
    # every worker re-filtering (and re-pickling) the full sales_df -- same
    # optimization backfill.py already uses.
    grouped = dict(tuple(sales_df.groupby(["sku", "store_id"])))
    tasks = [
        (sku, store_id, grouped[(sku, store_id)])
        for sku, store_id in pairs_list if (sku, store_id) in grouped
    ]

    # Capped, not just (cpu_count - 1) -- every worker is a fresh interpreter
    # that has to re-import torch + coreforecast from scratch (see DLL note
    # at top of file). torch's import alone is heavy; spawning one process
    # per core means that many simultaneous torch imports, which is what
    # was likely bogging the laptop down, not the actual forecasting work.
    workers = min(4, max(1, (os.cpu_count() or 4) - 1))
    print(f"Using {workers} worker processes (capped at 4 -- each has to reload "
          f"torch/coreforecast, so more workers isn't free here). First results "
          f"may take a minute or two to appear while those imports happen.", flush=True)

    all_rows = []
    completed = 0
    progress_every = max(1, len(tasks) // 20)  # ~20 updates total, regardless of scale

    # Connect BEFORE the executor starts, not after -- so each pair's rows
    # can be written to the DB the moment that pair finishes, instead of
    # holding everything in memory until every single pair is done. If the
    # machine loses power mid-run, only the pairs still in flight are
    # lost -- not the whole run. asyncpg connections/pools can't be shared
    # across process-pool workers, so this write step stays here in the
    # main process; only the CPU-bound forecasting itself is parallelized.
    repo = InventoryRepo()
    await repo.connect()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_one_pair, task): (task[0], task[1]) for task in tasks}
        for future in as_completed(futures):
            sku, store_id = futures[future]
            try:
                pair_rows = future.result()
            except Exception as exc:
                print(f"  pair (sku={sku}, store={store_id}) failed: {exc!r}", flush=True)
                pair_rows = []
            for row in pair_rows:
                await repo.insert_forecast(row)
            all_rows.extend(pair_rows)
            completed += 1
            if completed % progress_every == 0 or completed == len(tasks):
                print(f"  completed {completed}/{len(tasks)} pairs — {len(all_rows)} forecast rows written so far", flush=True)

    await repo.close()
    print(f"Done. {len(all_rows)} forecast rows written.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())