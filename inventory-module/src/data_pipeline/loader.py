"""
Data Loader
===========
Load and validate CSV data files
"""

import pandas as pd
from pathlib import Path
from typing import Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataLoader:
    """Load and validate inventory data"""
    
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
    
    def load_sales_history(self, filepath: Optional[Path] = None) -> pd.DataFrame:
        """Load sales history with validation"""
        if filepath is None:
            filepath = self.data_dir / "sales_history.csv"
        
        logger.info(f"Loading sales history from {filepath}")
        df = pd.read_csv(filepath, parse_dates=["date"])
        
        # Validation
        required_cols = ["date", "sku", "store_id", "quantity_sold"]
        missing = set(required_cols) - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in sales history: {missing}")
        
        logger.info(f"✓ Loaded {len(df):,} sales records")
        return df
    
    def load_stock_history(self, filepath: Optional[Path] = None) -> pd.DataFrame:
        """Load stock history with validation"""
        if filepath is None:
            filepath = self.data_dir / "stock_history.csv"
        
        logger.info(f"Loading stock history from {filepath}")
        df = pd.read_csv(filepath, parse_dates=["date"])
        
        required_cols = ["date", "sku", "store_id", "stock_level"]
        missing = set(required_cols) - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in stock history: {missing}")
        
        logger.info(f"✓ Loaded {len(df):,} stock records")
        return df
    
    def load_product_master(self, filepath: Optional[Path] = None) -> pd.DataFrame:
        """Load product master data"""
        if filepath is None:
            filepath = self.data_dir / "product_master.csv"
        
        logger.info(f"Loading product master from {filepath}")
        df = pd.read_csv(filepath)
        
        required_cols = ["sku", "lead_time_days", "moq", "unit_cost"]
        missing = set(required_cols) - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in product master: {missing}")
        
        logger.info(f"✓ Loaded {len(df)} products")
        return df
    
    def load_promotions(self, filepath: Optional[Path] = None) -> pd.DataFrame:
        """Load promotions calendar"""
        if filepath is None:
            filepath = self.data_dir / "promotions.csv"
        
        logger.info(f"Loading promotions from {filepath}")
        df = pd.read_csv(filepath, parse_dates=["start_date", "end_date"])
        
        logger.info(f"✓ Loaded {len(df)} promotional campaigns")
        return df
    
    def load_forecast(self, filepath: Path) -> pd.DataFrame:
        """Load TimesFM forecast results"""
        logger.info(f"Loading forecast from {filepath}")
        df = pd.read_csv(filepath, parse_dates=["date"])
        
        required_cols = ["date", "predicted_demand"]
        missing = set(required_cols) - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in forecast: {missing}")
        
        logger.info(f"✓ Loaded forecast for {len(df)} days")
        return df
    
    def load_all(self) -> dict:
        """Load all datasets"""
        return {
            "sales": self.load_sales_history(),
            "stock": self.load_stock_history(),
            "products": self.load_product_master(),
            "promotions": self.load_promotions()
        } 