"""
Generate Forecast Script
========================
Standalone script to generate demand forecasts for all SKUs
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from config.settings import RAW_DATA_DIR, FORECAST_DIR, FORECAST_OUTPUT_PATH
from src.data_pipeline.loader import DataLoader
from src.forecasting.timesfm_forecaster import TimesFMForecaster


def main():
    """Generate forecasts for all SKU-store combinations"""
    print("=" * 70)
    print(" Demand Forecast Generation")
    print("=" * 70 + "\n")

    # Load data
    loader = DataLoader(RAW_DATA_DIR)
    sales_df = loader.load_sales_history()

    # Get unique SKU-store pairs
    sku_store_pairs = sales_df[["sku", "store_id"]].drop_duplicates()
    print(f"Found {len(sku_store_pairs)} SKU-store combinations\n")

    # Initialize forecaster
    forecaster = TimesFMForecaster(horizon=30)

    # Generate forecasts (no save_dir — we handle saving ourselves below)
    print("Generating forecasts...\n")
    forecasts = forecaster.forecast_multiple(
        sales_df=sales_df,
        sku_store_pairs=list(sku_store_pairs.itertuples(index=False, name=None)),
    )

    if not forecasts:
        print("No forecasts were generated. Exiting.")
        return

    # Combine all forecasts
    combined_df = pd.concat(forecasts.values(), ignore_index=True)

    # Save 1 file per store
    print("Saving per-store forecast files...")
    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    for store_id, store_df in combined_df.groupby("store_id"):
        store_path = FORECAST_DIR / f"forecast_{store_id}.csv"
        store_df.to_csv(store_path, index=False)
        print(f"  Saved {store_path.name}  ({len(store_df)} rows)")

    # Save combined file
    print("\nSaving combined forecast file...")
    combined_df.to_csv(FORECAST_OUTPUT_PATH, index=False)
    print(f"  Combined forecast saved to: {FORECAST_OUTPUT_PATH}")

    # Summary
    print("\n" + "=" * 70)
    print(f"Generated {len(forecasts)} SKU-store forecasts")
    print(f"  Per-store files : {FORECAST_DIR}  ({combined_df['store_id'].nunique()} files)")
    print(f"  Combined file   : {FORECAST_OUTPUT_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()