"""Feature extraction for the demand sensing model.

Used both by the offline training pipeline (docs/inventory/scripts/
build_sensing_training_table.py) and the production job
(docs/inventory/scripts/run_sensing_job.py) — keep this the single
source of truth for what a "feature row" looks like so train/serve
never drift apart.

Events / weather matching notes
--------------------------------
inventory.events rows can be scoped at very different granularities
(sku-specific, store-specific, category-wide/national — the `scope`
column isn't a fixed enum we've fully enumerated). Rather than branch
on `scope` text, matching is *presence-driven*: a filter only applies
if the corresponding column is populated on that event row.
  - event.sku set         -> must equal this row's sku
  - event.store_id set    -> must equal this row's store_id
  - event.affected_categories set -> this sku's category (from
    product_category_df) must appear in that comma-separated list
A "national" event (sku and store_id both null) with only
affected_categories set will therefore match every sku in that
category, at every store — which is what rows like "Ramadan 2026"
need.

Weather is looked up from a pre-fetched weather_df (store_id, date ->
temp/precip) rather than calling an API per row — see
build_sensing_training_table.py / run_sensing_job.py for how that
gets populated (historical archive API for training, forecast API
for live serving).
"""
import pandas as pd

FEATURE_COLUMNS = [
    "baseline_demand", "recent_actual_avg", "active_promo",
    "promo_discount_pct", "upcoming_promo_7d", "stockout_flag_7d",
    "day_of_week",
    "active_event_uplift_pct", "upcoming_event_uplift_pct_7d",
    "expected_temp_c", "expected_precip_mm",
]


def _sku_category(sku, product_category_df):
    if product_category_df is None or len(product_category_df) == 0:
        return None
    match = product_category_df.loc[product_category_df.sku == sku, "category"]
    if len(match) == 0 or pd.isna(match.iloc[0]):
        return None
    return str(match.iloc[0]).strip().lower()


def _event_matches(event_row, sku, store_id, sku_category):
    if pd.notna(event_row.sku) and int(event_row.sku) != int(sku):
        return False
    if pd.notna(event_row.store_id) and str(event_row.store_id) != str(store_id):
        return False
    categories_raw = getattr(event_row, "affected_categories", None)
    if pd.notna(categories_raw) and str(categories_raw).strip():
        cats = {c.strip().lower() for c in str(categories_raw).split(",") if c.strip()}
        if sku_category is None or sku_category not in cats:
            return False
    return True


def _matching_events(events_df, sku, store_id, sku_category, window_start, window_end):
    """Events active/upcoming in [window_start, window_end], filtered to this sku/store."""
    if events_df is None or len(events_df) == 0:
        return events_df.iloc[0:0] if events_df is not None else pd.DataFrame()
    in_window = events_df[
        (events_df.start_date <= window_end) & (events_df.end_date >= window_start)
    ]
    if len(in_window) == 0:
        return in_window
    mask = in_window.apply(
        lambda r: _event_matches(r, sku, store_id, sku_category), axis=1
    )
    return in_window[mask]


def _weather_lookup(weather_df, store_id, forecast_date):
    if weather_df is None or len(weather_df) == 0:
        return None, None
    row = weather_df[
        (weather_df.store_id == store_id) & (weather_df.date == pd.Timestamp(forecast_date))
    ]
    if len(row) == 0:
        return None, None
    temp = row["temp_c"].iloc[0]
    precip = row["precip_mm"].iloc[0]
    return (
        float(temp) if pd.notna(temp) else None,
        float(precip) if pd.notna(precip) else None,
    )


def build_feature_row(sku, store_id, forecast_date, baseline_demand,
                       sales_df, promotions_df, stock_df,
                       events_df=None, weather_df=None, product_category_df=None) -> dict:
    recent = sales_df[
        (sales_df.sku == sku) & (sales_df.store_id == store_id)
        & (sales_df.record_date >= forecast_date - pd.Timedelta(days=7))
        & (sales_df.record_date < forecast_date)
    ]
    active_promo = promotions_df[
        (promotions_df.sku == sku)
        & (promotions_df.start_date <= forecast_date)
        & (promotions_df.end_date >= forecast_date)
    ]
    upcoming_promo = promotions_df[
        (promotions_df.sku == sku)
        & (promotions_df.start_date > forecast_date)
        & (promotions_df.start_date <= forecast_date + pd.Timedelta(days=7))
    ]
    stockout_recent = stock_df[
        (stock_df.sku == sku) & (stock_df.store_id == store_id)
        & (stock_df.record_date >= forecast_date - pd.Timedelta(days=7))
        & (stock_df.record_date < forecast_date)
    ]["is_stockout"].any() if len(stock_df) else False

    forecast_date = pd.Timestamp(forecast_date)
    sku_category = _sku_category(sku, product_category_df)

    active_events = _matching_events(
        events_df, sku, store_id, sku_category,
        window_start=forecast_date, window_end=forecast_date,
    )
    upcoming_events = _matching_events(
        events_df, sku, store_id, sku_category,
        window_start=forecast_date + pd.Timedelta(days=1),
        window_end=forecast_date + pd.Timedelta(days=7),
    )
    expected_temp_c, expected_precip_mm = _weather_lookup(weather_df, store_id, forecast_date)

    return {
        "baseline_demand": float(baseline_demand),
        "recent_actual_avg": recent["quantity_sold"].mean() if len(recent) else 0.0,
        "active_promo": int(len(active_promo) > 0),
        "promo_discount_pct": float(active_promo["discount_pct"].max()) if len(active_promo) else 0.0,
        "upcoming_promo_7d": int(len(upcoming_promo) > 0),
        "stockout_flag_7d": int(bool(stockout_recent)),
        "day_of_week": forecast_date.dayofweek,
        "active_event_uplift_pct": float(active_events["estimated_uplift_pct"].max()) if len(active_events) else 0.0,
        "upcoming_event_uplift_pct_7d": float(upcoming_events["estimated_uplift_pct"].max()) if len(upcoming_events) else 0.0,
        # NaN (not 0.0) when weather is missing — 0degC / 0mm are real
        # readings, so a silent 0.0 default would look like valid data.
        # Whether/how to impute is a training-table decision (see
        # build_sensing_training_table.py), not something to bake in here.
        "expected_temp_c": expected_temp_c if expected_temp_c is not None else float("nan"),
        "expected_precip_mm": expected_precip_mm if expected_precip_mm is not None else float("nan"),
    }