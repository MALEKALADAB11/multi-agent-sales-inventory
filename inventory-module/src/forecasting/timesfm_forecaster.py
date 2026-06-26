"""
TimesFM Forecaster
==================
Wrapper for Google's TimesFM model for demand forecasting
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
import logging
import timesfm
from timesfm import TimesFmHparams, TimesFmCheckpoint

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TimesFMForecaster:
    """
    TimesFM-based demand forecasting
    """
    
    def __init__(self, context_length: int = 512, horizon: int = 30):
        """
        Initialize forecaster
        
        Args:
            context_length: Historical window for forecasting
            horizon: Number of days to forecast
        """
        self.context_length = context_length
        self.horizon = horizon
        
        logger.info("Loading TimesFM model...")
        try:
            self.tfm = timesfm.TimesFm(
                hparams=TimesFmHparams(
                    context_len=self.context_length,
                    horizon_len=self.horizon,
                    input_patch_len=32,
                    output_patch_len=128,
                    num_layers=20,
                    model_dims=1280,
                    backend='cpu',  # Change to 'gpu' if GPU available
                ),
                checkpoint=TimesFmCheckpoint(
                    huggingface_repo_id="google/timesfm-1.0-200m-pytorch"
                ),
            )
            logger.info("✓ TimesFM model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load TimesFM model: {e}")
            raise
        
        logger.info(f"TimesFM Forecaster initialized (horizon={horizon} days)")
    
    def prepare_data(
        self, 
        sales_df: pd.DataFrame, 
        sku: str, 
        store_id: str
    ) -> pd.DataFrame:
        """
        Prepare sales data for forecasting
        
        Args:
            sales_df: Full sales history
            sku: Product SKU
            store_id: Store ID
        
        Returns:
            Filtered and sorted time series
        """
        # Filter for specific SKU and store
        ts_data = sales_df[
            (sales_df["sku"] == sku) & 
            (sales_df["store_id"] == store_id)
        ].copy()
        
        # Sort by date
        ts_data = ts_data.sort_values("date").reset_index(drop=True)
        
        if len(ts_data) == 0:
            raise ValueError(f"No data found for SKU={sku}, Store={store_id}")
        
        logger.info(f"Prepared {len(ts_data)} historical data points for {sku}")
        return ts_data
    
    def forecast(
        self, 
        sales_df: pd.DataFrame, 
        sku: str, 
        store_id: str,
        save_path: Optional[Path] = None
    ) -> pd.DataFrame:
        """
        Generate demand forecast
        
        Args:
            sales_df: Historical sales data
            sku: Product SKU
            store_id: Store ID
            save_path: Optional path to save forecast
        
        Returns:
            DataFrame with forecasted demand
        """
        # Prepare data
        ts_data = self.prepare_data(sales_df, sku, store_id)
        
        # Prepare input for TimesFM
        # Use the model's actual context_len (not the init param, which may differ)
        context_data = ts_data["quantity_sold"].values[-self.tfm.context_len:].astype(np.float32)
        context = context_data.reshape(1, -1)  # Shape: (1, context_len)
        
        logger.info(f"Forecasting with context of {len(context_data)} data points")
        
        # Generate forecast
        forecast_result = self.tfm.forecast(inputs=context, freq=[0])  # freq=0 for daily
        
        # Extract predictions
        predicted_demand = forecast_result[0][0, :self.horizon]
        
        # Ensure non-negative predictions
        predicted_demand = np.maximum(0, predicted_demand)

        # ── Promo adjustment ──────────────────────────────────────────────
        # Calculate historical promo multiplier from sales data,
        # then boost any forecast day that falls inside a known promo window.
        # This mirrors the notebook evaluation logic but applied to the real future.
        if "is_promo" in ts_data.columns:
            promo_sales = ts_data[ts_data["is_promo"] == 1]["quantity_sold"].mean()
            normal_sales = ts_data[ts_data["is_promo"] == 0]["quantity_sold"].mean()
            promo_multiplier = promo_sales / normal_sales if normal_sales > 0 else 2.5

            last_date_temp = ts_data["date"].max()
            forecast_dates_temp = pd.date_range(
                start=last_date_temp + pd.Timedelta(days=1),
                periods=self.horizon,
                freq="D" 
            )

            try:
                from src.pg_data_loader import load_promotions
                promos_df  = load_promotions(active_only=False)
                sku_promos = promos_df[promos_df["sku"] == sku]

                for i, forecast_date in enumerate(forecast_dates_temp):
                    is_promo_day = any(
                        row["start_date"] <= forecast_date <= row["end_date"]
                        for _, row in sku_promos.iterrows()
                    )
                    if is_promo_day:
                        predicted_demand[i] *= promo_multiplier
                        logger.info(f"  Promo boost on {forecast_date.date()}: x{promo_multiplier:.2f}")

            except Exception as e:
                logger.warning(f"Could not apply promo adjustment: {e}")
        # ─────────────────────────────────────────────────────────────────

        # Generate forecast dates
        last_date = ts_data["date"].max()
        forecast_dates = pd.date_range(
            start=last_date + pd.Timedelta(days=1),
            periods=self.horizon,
            freq="D"
        )
        
        # Create forecast DataFrame
        forecast_df = pd.DataFrame({
            "date": forecast_dates,
            "predicted_demand": predicted_demand,
            "sku": sku,
            "store_id": store_id
        })
        
        logger.info(f"✓ Generated {self.horizon}-day forecast for {sku}")
        logger.info(f"  Mean demand: {predicted_demand.mean():.2f} units/day")
        logger.info(f"  Total demand: {predicted_demand.sum():.0f} units")
        
        # Save if path provided
        if save_path:
            forecast_df.to_csv(save_path, index=False)
            logger.info(f"✓ Saved forecast to {save_path}")
        
        return forecast_df
    
    def forecast_multiple(
        self, 
        sales_df: pd.DataFrame, 
        sku_store_pairs: list,
        save_dir: Optional[Path] = None
    ) -> dict:
        """
        Forecast for multiple SKU-store combinations
        
        Args:
            sales_df: Sales history
            sku_store_pairs: List of (sku, store_id) tuples
            save_dir: Directory to save forecasts
        
        Returns:
            Dictionary mapping (sku, store) -> forecast DataFrame
        """
        forecasts = {}
        
        for sku, store_id in sku_store_pairs:
            try:
                save_path = None
                if save_dir:
                    save_path = save_dir / f"forecast_{sku}_{store_id}.csv"
                
                forecast = self.forecast(sales_df, sku, store_id, save_path)
                forecasts[(sku, store_id)] = forecast
                
            except Exception as e:
                logger.error(f"Failed to forecast {sku} at {store_id}: {e}")
                continue
        
        logger.info(f"✓ Completed {len(forecasts)} forecasts")
        return forecasts
