"""
Configuration Settings
======================
Central configuration for the inventory agent system.
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional, Literal

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Data directories
DATA_DIR = BASE_DIR / "data"
cleaned_DATA_DIR = DATA_DIR / "cleaned"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
FORECAST_DIR = DATA_DIR / "forecasts"

# Data file paths
STOCK_HISTORY_PATH = PROCESSED_DATA_DIR / "stock_history.csv"
SALES_HISTORY_PATH = PROCESSED_DATA_DIR / "sales_history.csv"
PRODUCT_MASTER_PATH = PROCESSED_DATA_DIR / "product_master.csv"
PROMOTIONS_PATH = PROCESSED_DATA_DIR / "promotions.csv"
FORECAST_OUTPUT_PATH = FORECAST_DIR / "timesFM_future_forecast.csv"

# Inventory thresholds
SAFETY_STOCK_MULTIPLIER = 1.2
CRITICAL_THRESHOLD_DAYS = 0.5  # Stock < 50% of lead time = CRITICAL
HIGH_THRESHOLD_DAYS = 1.0      # Stock < 100% of lead time = HIGH
MEDIUM_THRESHOLD_DAYS = 1.5    # Stock < 150% of lead time = MEDIUM

# Default values
DEFAULT_STORE = "I63"

# Business objective settings
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
        "description": "Maximize availability, premium safety"
    },
    "competitive": {
        "safety_stock_factor": 1.3,
        "service_level": 0.95,
        "description": "Proactive stocking, market presence"
    }
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # ══════════════════════════════════════════════════════════════════════
    # LLM Provider Configuration
    # ══════════════════════════════════════════════════════════════════════
    
    # Provider selection (groq, ollama, openai, anthropic, etc.)
    llm_provider: Literal["groq", "ollama", "openai", "anthropic"] = "ollama"
    llm_temperature: float = 0.0  # Default for reasoning tasks
    
    # ── Groq Configuration ────────────────────────────────────────────────
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    
    # ── Ollama Configuration ──────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:latest"
    
    # ── OpenAI Configuration ──────────────────────────────────────────────
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    
    # ── Anthropic Configuration ───────────────────────────────────────────
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    
    # ══════════════════════════════════════════════════════════════════════
    # Legacy fields (for backward compatibility)
    # ══════════════════════════════════════════════════════════════════════
    llm_model: Optional[str] = None  # Deprecated - use provider-specific model
    llm_base_url: Optional[str] = None  # Deprecated - use provider-specific base_url

    # MCP Configuration
    mcp_server_url: Optional[str] = None
    
    # App
    environment: str = "development"
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()

# Legacy exports (for backward compatibility)
GROQ_API_KEY = settings.groq_api_key
LLM_MODEL = settings.llm_model or settings.groq_model
LLM_TEMPERATURE = settings.llm_temperature
LLM_BASE_URL = settings.llm_base_url or settings.groq_base_url