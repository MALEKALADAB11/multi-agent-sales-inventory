"""
Configuration centrale du Sales Module.
"""
from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from functools import lru_cache


class SalesModuleConfig(BaseSettings):

    model_config = ConfigDict(
        env_file=".env",
        env_prefix="SALES_",
        extra="ignore",       # ← ignore les champs inconnus du .env
    )

    # General
    env: str = "development"
    debug: bool = True
    use_mock_data: bool = True

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # TimesFM
    timesfm_model_path: str = "./models/timesfm"
    timesfm_horizon_hours: int = 8
    timesfm_context_length: int = 512

    # Seuils urgence
    urgency_high_threshold: float = 0.30
    urgency_medium_threshold: float = 0.15
    default_daily_target: float = 15000.0

    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_context_ttl: int = 300

    # BigQuery
    bigquery_project: str = "ooredoo-retail-dev"
    bigquery_dataset: str = "retail_sales"
    bigquery_pos_table: str = "pos_transactions"

    # LangGraph
    langgraph_checkpointer: str = "memory"
    max_analyst_retries: int = 3


@lru_cache()
def get_config() -> SalesModuleConfig:
    return SalesModuleConfig()