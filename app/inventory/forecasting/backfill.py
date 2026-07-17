"""Walk-forward historical baseline generation — one-time bootstrap for
training data. Re-runs the (fixed) baseline engine as-of each historical
date using only data available before that date.

NOTE (fix applied on top of the implementation guide's Step 6 draft):
`timeseries_engine.forecast()` takes `sales_series: List[float]`, not a
DataFrame — confirmed by reading the real function signature. The guide's
draft passed the raw `history` DataFrame straight into `ts_forecast()`,
which would either throw (non-numeric columns) or silently misbehave.
The fix: extract the daily series with `extract_series_from_sales()`
first, exactly like `nodes.py` and `run_baseline_batch.py` already do,
then forecast on that.

Also note: `extract_series_from_sales()` only recognizes a date column
named "date" / "transaction_date" / "ds" (checked directly in the
source) — not "record_date". Raw `SELECT *` reads from
`inventory.sales_history` come back with column `record_date`. Without
renaming it to `date` first, `extract_series_from_sales()` silently
falls back to `[0.0] * 7` for every SKU/store (it swallows the missing
column rather than raising), which would quietly produce all-zero
baseline forecasts. `backfill_baseline()` below aliases the column
defensively so this can't happen silently.

NOTE (Windows access-violation crash — resolved): a 0xC0000005 crash
during backfill runs traced back to a native DLL load-order conflict
between numpy's bundled OpenBLAS and torch/coreforecast's bundled native
runtimes (both are dependencies pulled in transitively via
timeseries_engine.py). Fix: torch and coreforecast must be imported
before pandas/numpy in every process that will eventually import
timeseries_engine.

IMPORTANT for the parallel version below: with ProcessPoolExecutor on
Windows, each worker is a **freshly spawned interpreter** — it does NOT
inherit the parent process's sys.modules cache. That means the "just
import them first in the entry script" fix that worked for the
sequential version is NOT enough once we're multiprocessing: every
worker process re-imports this module from scratch and would hit the
exact same crash independently unless the import order is fixed at
THIS file's own top level too. Hence the reordered imports below.

NOTE (Timestamp/date TypeError — resolved): sales_df.record_date comes
back from the DB as plain datetime.date objects (object dtype), which
can't be compared against the pd.Timestamp values pd.date_range()
produces. Fixed by unconditionally coercing record_date to datetime64.

NOTE (performance — sequential version was ~66 hours for 4258 pairs):
each (sku, store_id) pair is fully independent of every other pair, so
this has been parallelized across processes, one pair per task. Sales
history is grouped once up front so each worker only receives the slice
of data relevant to its own pair, rather than re-filtering (or
re-pickling) the full multi-year sales_df per pair.
"""
import torch  # noqa: F401 — must load before pandas/numpy, see DLL note above
import coreforecast.exponentially_weighted  # noqa: F401 — must load before pandas/numpy

import os
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd
# CONFIRMED via nodes.py's own import: the function is named `forecast` inside
# timeseries_engine.py, aliased on import. v3 guessed `ts_forecast` as the real
# name — it isn't; match nodes.py's pattern exactly:
from .timeseries_engine import forecast as ts_forecast, extract_series_from_sales


def _process_one_pair(args):
    """Runs in a worker process — must be a top-level function so it can be pickled."""
    sku, store_id, group_df, start_date, end_date, horizon, step_days = args
    results = []
    for as_of_date in pd.date_range(start_date, end_date, freq=f"{step_days}D"):
        history = group_df[group_df.record_date < as_of_date]
        if len(history) < 90:
            continue
        series = extract_series_from_sales(history, sku, store_id, days_back=730)
        if len(series) < 90:
            continue
        result = ts_forecast(series, horizon=horizon)
        for i, val in enumerate(result["forecast_values"]):
            results.append({
                "sku": sku, "store_id": store_id, "as_of_date": as_of_date,
                "forecast_date": as_of_date + pd.Timedelta(days=i + 1),
                "baseline_demand": val,
            })
    return results


def backfill_baseline(sku_store_pairs, start_date, end_date, sales_df, horizon=30,
                       step_days=21, max_workers=None):
    sales_df = sales_df.copy()
    # Always coerce record_date to a real datetime64 dtype — the DB driver hands back
    # plain datetime.date objects (object dtype), which can't be compared against the
    # pd.Timestamp values that pd.date_range() produces for as_of_date below.
    sales_df["record_date"] = pd.to_datetime(sales_df["record_date"])
    if "date" not in sales_df.columns:
        sales_df["date"] = sales_df["record_date"]

    pairs_list = list(sku_store_pairs)
    total_pairs = len(pairs_list)
    print(f"Starting backfill for {total_pairs} sku/store pairs (parallel)...", flush=True)

    # Pre-group once so each worker gets only the rows it needs, instead of every
    # worker re-filtering (and every task re-pickling) the full multi-year sales_df.
    grouped = dict(tuple(sales_df.groupby(["sku", "store_id"])))

    tasks = []
    skipped = 0
    for sku, store_id in pairs_list:
        group_df = grouped.get((sku, store_id))
        if group_df is None:
            skipped += 1
            continue
        tasks.append((sku, store_id, group_df, start_date, end_date, horizon, step_days))
    if skipped:
        print(f"  skipped {skipped} pairs with no matching sales history", flush=True)

    workers = max_workers or max(1, (os.cpu_count() or 4) - 1)
    print(f"Using {workers} worker processes", flush=True)

    results = []
    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_one_pair, task): (task[0], task[1]) for task in tasks}
        for future in as_completed(futures):
            sku, store_id = futures[future]
            try:
                pair_results = future.result()
            except Exception as exc:
                print(f"  pair (sku={sku}, store={store_id}) failed: {exc!r}", flush=True)
                pair_results = []
            results.extend(pair_results)
            completed += 1
            if completed % 5 == 0 or completed == len(tasks):
                print(f"  completed {completed}/{len(tasks)} pairs — {len(results)} rows so far", flush=True)

    return pd.DataFrame(results)