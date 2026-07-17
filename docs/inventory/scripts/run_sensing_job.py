import sys, asyncio, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import requests
from app.inventory.forecasting.sensing_model import SensingModel
from app.inventory.forecasting.sensing_features import build_feature_row
from app.inventory.repositories.inventory_repo import InventoryRepo
from app.inventory.tools.internal.stock_tools import _query

# Anchored to this script's own file location, not the process's current
# working directory -- a plain relative path here would resolve
# differently depending on whether you run this by hand from the repo
# root vs. Task Scheduler running it with "Start in" set to the scripts
# folder. Using __file__ makes it identical either way.
DATA_DIR = Path(__file__).resolve().parent / "data"  # docs/inventory/scripts/data
MODEL_PATH = str(
    Path(__file__).resolve().parents[3]
    / "app" / "inventory" / "forecasting" / "models" / "sensing_model_v1.ubj"
)
IMPUTE_MEDIANS_PATH = str(DATA_DIR / "weather_impute_medians.json")
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SENSING_HORIZON_DAYS = 7  # was 14 -- beyond ~7 days out, recent_actual_avg and
# stockout_flag_7d look at a window that's entirely in the future relative to
# today (no actual sales/stock data exists yet for those dates), so they'd
# silently be 0.0/False rather than real signal. Capping the horizon at 7
# keeps every corrected forecast's "recent" window fully in the past.


def fetch_live_weather(stores_df, days_ahead):
    """Daily mean temp + total precip per store for the next `days_ahead` days.

    Uses Open-Meteo's live /v1/forecast endpoint -- unlike the training
    pipeline (build_sensing_training_table.py), which needs the
    historical /v1/archive endpoint since it's scoring the past.
    One call per store for the whole horizon, not one per (sku, store,
    date) row -- there's no point re-fetching the same store's forecast
    for every sku/date combination.
    """
    frames = []
    for store in stores_df.itertuples(index=False):
        if pd.isna(store.latitude) or pd.isna(store.longitude):
            print(f"  skipping live weather for store {store.store_id} -- no lat/lon")
            continue
        resp = requests.get(WEATHER_FORECAST_URL, params={
            "latitude": store.latitude,
            "longitude": store.longitude,
            "forecast_days": min(days_ahead, 16),  # Open-Meteo's forecast_days cap
            "daily": "temperature_2m_mean,precipitation_sum",
            "timezone": "auto",
        }, timeout=30)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        if not daily.get("time"):
            continue
        frames.append(pd.DataFrame({
            "store_id": store.store_id,
            "date": pd.to_datetime(daily["time"]),
            "temp_c": daily.get("temperature_2m_mean"),
            "precip_mm": daily.get("precipitation_sum"),
        }))
    if not frames:
        return pd.DataFrame(columns=["store_id", "date", "temp_c", "precip_mm"])
    return pd.concat(frames, ignore_index=True)


async def main():
    model = SensingModel.load(MODEL_PATH)
    repo = InventoryRepo()
    await repo.connect()

    sales_df = pd.DataFrame(_query("SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '30 days'"))
    promotions_df = pd.DataFrame(_query("SELECT * FROM inventory.promotions"))
    stock_df = pd.DataFrame(_query("SELECT * FROM inventory.stock_history WHERE record_date >= CURRENT_DATE - INTERVAL '14 days'"))

    events_df = pd.DataFrame(_query("SELECT * FROM inventory.events"))
    for col in ("start_date", "end_date"):
        events_df[col] = pd.to_datetime(events_df[col])

    product_category_df = pd.DataFrame(_query("SELECT sku, category FROM inventory.product_master"))

    # latitude/longitude only live on sales.boutiques -- inventory.stores
    # is a view over it that drops those two columns.
    stores_df = pd.DataFrame(_query("SELECT store_id, latitude, longitude FROM sales.boutiques"))

    weather_df = fetch_live_weather(stores_df, days_ahead=SENSING_HORIZON_DAYS + 1)

    # Same medians the training table was imputed with -- so a missing
    # live weather reading gets filled the same way a missing training
    # reading was, instead of passing raw NaN into a model that never
    # saw NaN at train time (see build_sensing_training_table.py).
    impute_medians = {}
    if Path(IMPUTE_MEDIANS_PATH).exists():
        with open(IMPUTE_MEDIANS_PATH) as f:
            impute_medians = json.load(f)
    else:
        print(f"  WARNING: {IMPUTE_MEDIANS_PATH} not found -- missing weather will be "
              f"passed as raw NaN, which may not match how the model was trained.")

    pairs = sales_df[["sku", "store_id"]].drop_duplicates().itertuples(index=False, name=None)

    for sku, store_id in pairs:
        # get_forecast_range, not get_forecasts_for_date — see 1.8
        baseline_rows = await repo.get_forecast_range(sku, store_id, days=SENSING_HORIZON_DAYS)
        for row in baseline_rows:
            if row["baseline_demand"] is None:
                continue
            features = build_feature_row(
                sku, store_id, row["forecast_date"], row["baseline_demand"],
                sales_df, promotions_df, stock_df,
                events_df=events_df, weather_df=weather_df, product_category_df=product_category_df,
            )
            for col, median_val in impute_medians.items():
                if features.get(col) is not None and pd.isna(features[col]):
                    features[col] = median_val
            corrected = model.predict(features, row["baseline_demand"])
            await repo.insert_forecast({
                "sku": sku, "store_id": store_id, "forecast_date": row["forecast_date"],
                "corrected_demand": corrected,
                "correction_method": "sensing_model_v1",
                "correction_features": features,
            })

    await repo.close()

if __name__ == "__main__":
    asyncio.run(main())