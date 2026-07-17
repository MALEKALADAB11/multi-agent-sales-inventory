import torch  # noqa: F401
# ^ MUST be imported before pandas/numpy — same DLL load-order issue as coreforecast
#   below, discovered one layer deeper in the import chain (torch is imported inside
#   timeseries_engine.py, further down than statsforecast/coreforecast). If pandas/numpy
#   load first, importing torch fails with:
#   OSError: [WinError 1114] ... Error loading "...\torch\lib\c10.dll" ...
#   Confirmed fixed by moving this import ahead of pandas.
import coreforecast.exponentially_weighted  # noqa: F401
# ^ MUST be imported before pandas/numpy — Windows DLL load-order conflict
#   between coreforecast's bundled native runtime and numpy's bundled
#   OpenBLAS. If pandas/numpy load first, importing coreforecast (pulled
#   in transitively via backfill -> timeseries_engine -> statsforecast)
#   crashes the process with exit code -1073741819 (0xC0000005, access
#   violation) and no Python traceback. Confirmed via `python -X faulthandler`.

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # -> back/

import pandas as pd
from app.inventory.forecasting.backfill import backfill_baseline
from app.inventory.tools.internal.stock_tools import _query

DATA_DIR = Path(__file__).resolve().parent / "data"  # docs/inventory/scripts/data
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- TEMPORARY: run on the top-selling pairs to unblock benchmarking/frontend work today ---
# Set to None to run the full ~4258-pair backfill (the "real" run — do this overnight or in
# the background once the pipeline is confirmed working end-to-end on this sample).
TOP_N_PAIRS = 400


def main():
    sales_df = pd.DataFrame(_query(
        "SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '24 months'"
    ))
    pairs_full = sales_df[["sku", "store_id"]].drop_duplicates()

    if TOP_N_PAIRS is not None:
        volume_by_pair = (
            sales_df.groupby(["sku", "store_id"])["quantity_sold"].sum()
            .sort_values(ascending=False)
        )
        top_index = volume_by_pair.head(TOP_N_PAIRS).index
        pairs_df = pairs_full.set_index(["sku", "store_id"]).loc[
            pairs_full.set_index(["sku", "store_id"]).index.intersection(top_index)
        ].reset_index()
        print(f"TOP_N_PAIRS={TOP_N_PAIRS} set — running on the top {len(pairs_df)} "
              f"pairs by sales volume out of {len(pairs_full)} total. "
              f"Set TOP_N_PAIRS=None for the real full run.", flush=True)
    else:
        pairs_df = pairs_full

    pairs = list(pairs_df.itertuples(index=False, name=None))
    result = backfill_baseline(pairs, sales_df.record_date.min(),
                                sales_df.record_date.max() - pd.Timedelta(days=30), sales_df)
    result.to_parquet(DATA_DIR / "backfill_baseline.parquet")
    print(f"Backfilled {len(result)} rows")


if __name__ == "__main__":
    main()