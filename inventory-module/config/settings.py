"""
Configuration Settings
======================
Central configuration for the inventory agent system.
"""

import os
from pathlib import Path
from typing import Optional, Literal
from dotenv import load_dotenv

# Load .env from inventory-module root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

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


# ─────────────────────────────────────────────────────────────
# Business constants
# ─────────────────────────────────────────────────────────────
SAFETY_STOCK_MULTIPLIER = 1.2
CRITICAL_THRESHOLD_DAYS = 0.5
HIGH_THRESHOLD_DAYS = 1.0
MEDIUM_THRESHOLD_DAYS = 1.5

DEFAULT_STORE = "I63"

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

    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # Legacy
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None

    mcp_server_url: Optional[str] = None

    # Database - READ FROM ENVIRONMENT
    db_host: str = os.getenv("DOCKER_DB_HOST", "localhost")
    db_port: int = int(os.getenv("DOCKER_DB_PORT", "5433"))
    db_name: str = os.getenv("DOCKER_DB_NAME", "asc_db")
    db_user: str = os.getenv("DOCKER_DB_USER", "asc_user")
    db_password: str = os.getenv("DOCKER_DB_PASSWORD", "asc_password")

    # App
    environment: str = os.getenv("ENVIRONMENT", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


# ─────────────────────────────────────────────────────────────
# Global instance
# ─────────────────────────────────────────────────────────────
settings = Settings()

# Backward compatibility
GROQ_API_KEY = settings.groq_api_key
LLM_MODEL = settings.llm_model or settings.groq_model
LLM_TEMPERATURE = settings.llm_temperature
LLM_BASE_URL = settings.llm_base_url or settings.groq_base_url