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

    # ── Feature Flags v5 ─────────────────────────────────────────────────────
    # Phase 1 — State Bus
    enable_state_bus:        bool = False
    enable_circuit_breaker:  bool = False

    # Phase 2 — Inventory Cross-Reference
    enable_inventory_sync:   bool = False
    inv_snapshot_ttl_s:      int  = 600   # 10 min

    # Phase 3 — Critique Agent
    enable_critique_agent:   bool = False
    critique_min_score:      float = 0.80  # seuil passage Coach
    critique_max_cycles:     int   = 2     # nb révisions max

    # Phase 4 — Supervisor
    enable_supervisor:       bool = False
    hitl_gap_threshold:      float = 0.70  # gap > 70% → HITL
    hitl_timeout_s:          int   = 120

    # Phase 5 — Feedback Loop
    enable_feedback_loop:    bool = False
    feedback_cron_hour:      int  = 21     # 21h chaque soir

    # Phase 6 — Multi-Store + Checkpointing
    enable_multi_store:      bool = False
    max_concurrent_stores:   int  = 5
    enable_pg_checkpointing: bool = False
    pg_checkpointer_url:     str  = "postgresql://postgres:admin@localhost:5432/ooredoo_sales"

    # ── Inventory module connection ──────────────────────────────────────────
    inventory_api_url:       str  = "http://localhost:8001"


@lru_cache()
def get_config() -> SalesModuleConfig:
    return SalesModuleConfig()
