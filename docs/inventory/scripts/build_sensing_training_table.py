import sys
import time
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # -> back/

import pandas as pd
import requests
from app.inventory.forecasting.sensing_features import build_feature_row, FEATURE_COLUMNS
from app.inventory.tools.internal.stock_tools import _query

DATA_DIR = Path(__file__).resolve().parent / "data"  # docs/inventory/scripts/data
WEATHER_CACHE_PATH = DATA_DIR / "weather_cache.parquet"
WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_historical_weather(stores_df, start_date, end_date):
    """Daily mean temp + total precip per store for [start_date, end_date].

    Uses Open-Meteo's *historical/archive* endpoint, not the live
    /v1/forecast one -- the forecast endpoint only returns current/future
    weather and backfill_baseline.parquet covers up to ~2 years in the
    past, so /v1/forecast can't answer for those dates. run_sensing_job.py
    (live serving) is the one that should call /v1/forecast instead.

    One API call per store for the whole date range (not per row) --
    with ~2 years of backfill rows per store, per-row calls would be both
    slow and pointless since the archive endpoint returns a full range in
    one response. Cached to weather_cache.parquet so re-running this
    script (e.g. after tweaking other features) doesn't re-hit the API.

    Note: Open-Meteo's ERA5 archive typically lags ~5 days behind today,
    so if backfill's forecast_date range runs close to the present, the
    most recent few days per store may come back as NaN -- handled via
    imputation in main(), not silently zero-filled here.
    """
    cache = pd.DataFrame(columns=["store_id", "date", "temp_c", "precip_mm"])
    if Path(WEATHER_CACHE_PATH).exists():
        cache = pd.read_parquet(WEATHER_CACHE_PATH)
        cache["date"] = pd.to_datetime(cache["date"])

    fetched = [cache]
    expected_days = (end_date - start_date).days + 1
    for store in stores_df.itertuples(index=False):
        have = cache[
            (cache.store_id == store.store_id)
            & (cache.date >= start_date) & (cache.date <= end_date)
        ]
        if len(have) >= expected_days:
            continue  # already cached for this store/date range

        if pd.isna(store.latitude) or pd.isna(store.longitude):
            print(f"  skipping weather for store {store.store_id} -- no lat/lon in sales.boutiques", flush=True)
            continue

        resp = requests.get(WEATHER_ARCHIVE_URL, params={
            "latitude": store.latitude,
            "longitude": store.longitude,
            "start_date": start_date.date().isoformat(),
            "end_date": end_date.date().isoformat(),
            "daily": "temperature_2m_mean,precipitation_sum",
            "timezone": "auto",
        }, timeout=30)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        if not daily.get("time"):
            continue

        fetched.append(pd.DataFrame({
            "store_id": store.store_id,
            "date": pd.to_datetime(daily["time"]),
            "temp_c": daily.get("temperature_2m_mean"),
            "precip_mm": daily.get("precipitation_sum"),
        }))
        time.sleep(0.2)  # be polite to the free API tier

    weather_df = pd.concat(fetched, ignore_index=True).drop_duplicates(["store_id", "date"])
    weather_df.to_parquet(WEATHER_CACHE_PATH)
    return weather_df


def _query_df(sql, label, required=True):
    """pd.DataFrame(_query(sql)), but fails loudly and immediately if the
    query came back with 0 rows -- instead of returning a columnless empty
    DataFrame that crashes cryptically several lines later (e.g. a
    KeyError on a column that would exist if there were any rows at all).
    """
    rows = _query(sql)
    df = pd.DataFrame(rows)
    print(f"  {label}: {len(df)} rows", flush=True)
    if required and len(df) == 0:
        raise RuntimeError(
            f"Query for '{label}' returned 0 rows -- check the SQL/date "
            f"window against what's actually in your DB before continuing.\n"
            f"  SQL: {sql}"
        )
    return df


def main():
    backfill_df = pd.read_parquet(DATA_DIR / "backfill_baseline.parquet")
    backfill_df["forecast_date"] = pd.to_datetime(backfill_df["forecast_date"])

    print("Querying source tables...", flush=True)
    sales_df = _query_df(
        "SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '18 months'",
        "sales_df", required=True,
    )
    sales_df["record_date"] = pd.to_datetime(sales_df["record_date"])

    promotions_df = _query_df("SELECT * FROM inventory.promotions", "promotions_df", required=False)
    for col in ("start_date", "end_date"):
        if col in promotions_df.columns:
            promotions_df[col] = pd.to_datetime(promotions_df[col])

    stock_df = _query_df(
        "SELECT * FROM inventory.stock_history WHERE record_date >= CURRENT_DATE - INTERVAL '18 months'",
        "stock_df", required=False,
    )
    if len(stock_df):
        stock_df["record_date"] = pd.to_datetime(stock_df["record_date"])

    events_df = _query_df("SELECT * FROM inventory.events", "events_df", required=False)
    for col in ("start_date", "end_date"):
        if col in events_df.columns:
            events_df[col] = pd.to_datetime(events_df[col])

    product_category_df = _query_df(
        "SELECT sku, category FROM inventory.product_master", "product_category_df", required=False
    )

    # latitude/longitude only live on sales.boutiques -- inventory.stores
    # is a view over it that drops those two columns, so query the
    # underlying table directly.
    stores_df = _query_df(
        "SELECT store_id, latitude, longitude FROM sales.boutiques", "stores_df", required=False
    )

    print("Fetching/loading historical weather (cached to weather_cache.parquet)...", flush=True)
    weather_df = fetch_historical_weather(
        stores_df,
        start_date=backfill_df["forecast_date"].min(),
        end_date=backfill_df["forecast_date"].max(),
    )

    # actual demand on forecast_date, keyed by (sku, store_id, record_date)
    actuals = sales_df.set_index(["sku", "store_id", "record_date"])["quantity_sold"]

    # THE bottleneck fix: build_feature_row filters sales_df/stock_df by
    # (sku, store_id) + a date window on every single call. Passing the
    # full 1.5M/2M-row tables means every training row re-scans the whole
    # table from scratch -- with a large backfill_df that's hundreds of
    # billions of comparisons, easily 30+ minutes. Grouping once up front
    # (same trick backfill.py already uses) means each row only searches
    # within its own pair's slice -- typically hundreds of rows, not
    # millions. promotions_df/product_category_df/events_df are small
    # enough (635 / 3716 / 33 rows) that this isn't worth doing for them.
    print("Grouping sales/stock history by (sku, store_id) for fast lookup...", flush=True)
    sales_grouped = dict(tuple(sales_df.groupby(["sku", "store_id"])))
    stock_grouped = dict(tuple(stock_df.groupby(["sku", "store_id"]))) if len(stock_df) else {}
    empty_sales = sales_df.iloc[0:0]
    empty_stock = stock_df.iloc[0:0]

    rows = []
    total = len(backfill_df)
    for i, row in enumerate(backfill_df.itertuples(index=False)):
        if i % 5000 == 0:
            print(f"  processed {i}/{total} backfill rows...", flush=True)
        key3 = (row.sku, row.store_id, row.forecast_date)
        if key3 not in actuals.index:
            continue
        actual_demand = actuals.loc[key3]
        if isinstance(actual_demand, pd.Series):  # duplicate rows for same day, guard against it
            actual_demand = actual_demand.sum()

        key2 = (row.sku, row.store_id)
        sales_slice = sales_grouped.get(key2, empty_sales)
        stock_slice = stock_grouped.get(key2, empty_stock)

        features = build_feature_row(
            row.sku, row.store_id, row.forecast_date, row.baseline_demand,
            sales_slice, promotions_df, stock_slice,
            events_df=events_df, weather_df=weather_df, product_category_df=product_category_df,
        )
        features["actual_demand"] = float(actual_demand)
        rows.append(features)

    training_df = pd.DataFrame(rows, columns=FEATURE_COLUMNS + ["actual_demand"])

    # RandomForestRegressor (one of the candidates benchmarked in step 1)
    # can't handle NaN natively, unlike LightGBM/XGBoost -- so missing
    # weather (dates outside Open-Meteo's archive coverage, or a store
    # with no lat/lon) gets median-imputed here. This is a training-table
    # decision, not a build_feature_row one.
    #
    # The medians used are saved to weather_impute_medians.json so
    # run_sensing_job.py can apply the *same* fill values live, instead of
    # passing raw NaN into a model that never actually saw NaN during
    # training (it saw the median). Without this, LightGBM's default
    # missing-value routing at inference would behave differently from
    # what the model learned.
    impute_medians = {}
    for col in ("expected_temp_c", "expected_precip_mm"):
        n_missing = training_df[col].isna().sum()
        median_val = training_df[col].median()
        if pd.isna(median_val):
            # Every row is missing (e.g. zero stores have lat/lon, like
            # right now) -- median() of an all-NaN column is itself NaN,
            # so fillna(median) would silently do nothing and leave
            # RandomForest broken. Fall back to 0.0 and say so loudly:
            # this means the weather feature currently carries no signal
            # at all, not that it's slightly noisy.
            median_val = 0.0
            print(f"  WARNING: {col} has NO valid values at all (0/{len(training_df)}) -- "
                  f"every store is missing lat/lon in sales.boutiques. Filling with 0.0. "
                  f"This feature will contribute nothing until store coordinates exist.", flush=True)
        else:
            median_val = float(median_val)
        impute_medians[col] = median_val
        if n_missing:
            print(f"  {col}: {n_missing}/{len(training_df)} missing, imputing with {median_val:.2f}", flush=True)
        training_df[col] = training_df[col].fillna(median_val)

    with open(DATA_DIR / "weather_impute_medians.json", "w") as f:
        json.dump(impute_medians, f)

    training_df.to_parquet(DATA_DIR / "training_table.parquet")
    print(f"Built training table with {len(training_df)} rows", flush=True)


if __name__ == "__main__":
    main()