"""
Configuration Settings
======================
Central configuration for the inventory agent system.
"""

import os
from pathlib import Path
from typing import Optional, Literal
from dotenv import load_dotenv
from app.core.config import DEFAULT_STORE_ID

# Load .env from project root (not inventory module)
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# ─────────────────────────────────────────────────────────────
# Base paths
# ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
CLEANED_DATA_DIR = DATA_DIR / "cleaned"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
FORECAST_DIR = DATA_DIR / "forecasts"

STOCK_HISTORY_PATH = PROCESSED_DATA_DIR / "stock_history.csv"
SALES_HISTORY_PATH = PROCESSED_DATA_DIR / "sales_history.csv"
PRODUCT_MASTER_PATH = PROCESSED_DATA_DIR / "product_master.csv"
PROMOTIONS_PATH = PROCESSED_DATA_DIR / "promotions.csv"
FORECAST_OUTPUT_PATH = FORECAST_DIR / "timesFM_future_forecast.csv"

# Demand sensing layer (baseline + LightGBM correction, see
# app/inventory/forecasting/) — env-overridable, same style as the rest
# of this file.
SENSING_MODEL_PATH = os.getenv(
    "SENSING_MODEL_PATH",
    str(BASE_DIR / "app" / "inventory" / "forecasting" / "models" / "sensing_model_v1.txt"),
)
BASELINE_DAYS_BACK = int(os.getenv("BASELINE_DAYS_BACK", "730"))
SENSING_HORIZON_DAYS = int(os.getenv("SENSING_HORIZON_DAYS", "14"))


# ─────────────────────────────────────────────────────────────
# Business constants
# ─────────────────────────────────────────────────────────────
SAFETY_STOCK_MULTIPLIER = 1.2
CRITICAL_THRESHOLD_DAYS = 0.5
HIGH_THRESHOLD_DAYS = 1.0
MEDIUM_THRESHOLD_DAYS = 1.5

DEFAULT_STORE = DEFAULT_STORE_ID

BUSINESS_OBJECTIVE_SETTINGS = {
    "cost": {
        "safety_stock_factor": 0.8,
        "service_level": 0.75,
        "description": "Minimize spending, accept moderate stockout risk"
    },
    "balanced": {
        "safety_stock_factor": 1.0,
        "service_level": 0.90,
        "description": "Standard safety level"
    },
    "service_level": {
        "safety_stock_factor": 1.5,
        "service_level": 0.98,
        "description": "Maximize availability"
    },
    "competitive": {
        "safety_stock_factor": 1.3,
        "service_level": 0.95,
        "description": "Proactive stocking"
    }
}


# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────
class Settings:
    """
    Inventory module settings.
    """

    # LLM config - READ FROM ENVIRONMENT
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))

    groq_api_key_1: Optional[str] = os.getenv("GROQ_API_KEY_1")
    groq_api_key_2: Optional[str] = os.getenv("GROQ_API_KEY_2")
    groq_api_key_3: Optional[str] = os.getenv("GROQ_API_KEY_3")
    groq_api_key_4: Optional[str] = os.getenv("GROQ_API_KEY_4")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # ── Mistral (API directe La Plateforme — quota indépendant d'OpenRouter) ──
    mistral_api_key:  Optional[str] = os.getenv("MISTRAL_API_KEY")
    mistral_base_url: str = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
    _mistral_default: str = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    # FAST     — analyse/contexte (rapide + économique)
    mistral_model_fast:     str = os.getenv("MISTRAL_MODEL_FAST",     "open-mistral-nemo")
    # SMART    — décision/coach/stratégie (raisonnement fort)
    mistral_model_smart:    str = os.getenv("MISTRAL_MODEL_SMART",    "mistral-large-latest")
    # GUARDIAN — guardrail/critique (fiable + structuré)
    mistral_model_guardian: str = os.getenv("MISTRAL_MODEL_GUARDIAN", _mistral_default)

    # ── OpenRouter (accès unifié à tous les modèles) ──────────────────────
    openrouter_api_key:   Optional[str] = os.getenv("OPENROUTER_API_KEY")
    openrouter_base_url:  str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_site_url:  str = os.getenv("OPENROUTER_SITE_URL", "https://ooredoo.tn")
    openrouter_app_name:  str = os.getenv("OPENROUTER_APP_NAME", "Ooredoo Retail Coach")

    # Modèles tiérés OpenRouter
    # Fallback : OPENROUTER_MODEL (défini dans .env) si la var spécifique est absente
    _or_default: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
    # FAST  — analyse/contexte (rapide + économique)
    openrouter_model_fast:     str = os.getenv("OPENROUTER_MODEL_FAST",     _or_default)
    # SMART — décision/coach (précis + raisonnement)
    openrouter_model_smart:    str = os.getenv("OPENROUTER_MODEL_SMART",    _or_default)
    # GUARDIAN — guardrail/critique (fiable + structuré)
    openrouter_model_guardian: str = os.getenv("OPENROUTER_MODEL_GUARDIAN", _or_default)

    # Legacy
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None

    mcp_server_url: Optional[str] = None

    # Database - READ FROM ENVIRONMENT
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "ooredoo_sales")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_password: str = os.getenv("DB_PASSWORD", "root")

    # App
    environment: str = os.getenv("ENVIRONMENT", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


# ─────────────────────────────────────────────────────────────
# Global instance
# ─────────────────────────────────────────────────────────────
settings = Settings()

# Backward compatibility
LLM_MODEL = settings.llm_model or settings.groq_model
LLM_TEMPERATURE = settings.llm_temperature
LLM_BASE_URL = settings.llm_base_url or settings.groq_base_url