"""
inventory-module/config/settings.py — Configuration inventory.
Utilise shared_module.config comme source unique de vérité.
"""

import sys, os

from pathlib import Path
from app.core.config import config as shared_config

# ── Chemins ───────────────────────────────────────────────────────────────────
BASE_DIR             = Path(__file__).resolve().parent.parent
DATA_DIR             = BASE_DIR / "data"
CLEANED_DATA_DIR     = DATA_DIR / "cleaned"
PROCESSED_DATA_DIR   = DATA_DIR / "processed"
FORECAST_DIR         = DATA_DIR / "forecasts"

STOCK_HISTORY_PATH   = PROCESSED_DATA_DIR / "stock_history.csv"
SALES_HISTORY_PATH   = PROCESSED_DATA_DIR / "sales_history.csv"
PRODUCT_MASTER_PATH  = PROCESSED_DATA_DIR / "product_master.csv"
FORECAST_OUTPUT_PATH = FORECAST_DIR / "timesFM_future_forecast.csv"

# ── Business constants ────────────────────────────────────────────────────────
SAFETY_STOCK_MULTIPLIER = 1.2
CRITICAL_THRESHOLD_DAYS = 0.5
HIGH_THRESHOLD_DAYS     = 1.0
MEDIUM_THRESHOLD_DAYS   = 1.5

DEFAULT_STORE = shared_config.DEFAULT_STORE_ID  # "I63"

BUSINESS_OBJECTIVE_SETTINGS = {
    "cost":          {"safety_stock_factor": 0.8,  "service_level": 0.75},
    "balanced":      {"safety_stock_factor": 1.0,  "service_level": 0.90},
    "service_level": {"safety_stock_factor": 1.5,  "service_level": 0.98},
    "competitive":   {"safety_stock_factor": 1.3,  "service_level": 0.95},
}

# ── Poids des signaux contextuels (fallback rule-based quand LLM indispo) ─────
# Surchargeables via variables d'environnement ou table inventory.signal_weights.
import os as _os
SIGNAL_WEIGHTS = {
    "promo_factor":      float(_os.getenv("SIGNAL_PROMO_FACTOR",    "0.5")),   # discount_pct × factor
    "holiday_uplift_pct": float(_os.getenv("SIGNAL_HOLIDAY_UPLIFT",  "10.0")), # % uplift par jour férié
    "bad_weather_impact": float(_os.getenv("SIGNAL_WEATHER_IMPACT",  "-3.0")), # % par jour de pluie forte
    "confidence_min_sample": int(_os.getenv("SIGNAL_CONF_SAMPLE",   "50")),    # seuil sample → medium
}


class Settings:
    """
    Settings inventory — toutes les valeurs viennent de shared_module.config.
    """
    # LLM
    llm_provider:   str   = "ollama"
    llm_temperature: float = 0.0
    ollama_base_url: str  = shared_config.OLLAMA_BASE_URL
    ollama_model:   str   = shared_config.OLLAMA_MODEL_ANALYST

    # DB — via shared_module (schéma inventory)
    db_host:     str = shared_config.DB_HOST
    db_port:     int = shared_config.DB_PORT
    db_name:     str = shared_config.DB_NAME
    db_user:     str = shared_config.DB_USER
    db_password: str = shared_config.DB_PASSWORD
    db_schema:   str = shared_config.SCHEMA_INVENTORY

    # App
    environment: str = shared_config.ENVIRONMENT
    log_level:   str = shared_config.LOG_LEVEL


settings = Settings()
