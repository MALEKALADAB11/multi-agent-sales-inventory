"""
shared_module/config.py — Configuration centrale partagée.
Utilisé par sales-module, inventory-module et monitoring-module.
Charge le .env depuis la racine du projet.
"""

import os
from pathlib import Path
from functools import lru_cache
from dotenv import load_dotenv

# Chercher le .env à la racine du projet (remonte jusqu'à 3 niveaux)
def _find_env() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(4):
        candidate = current / ".env"
        if candidate.exists():
            return candidate
        current = current.parent
    return Path(".env")

load_dotenv(_find_env(), override=False)


class Config:
    """Configuration unique partagée entre tous les modules."""

    # ── PostgreSQL ─────────────────────────────────────────────
    DB_HOST:     str = os.getenv("DB_HOST",     "localhost")
    DB_PORT:     int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME:     str = os.getenv("DB_NAME",     "ooredoo_sales")
    DB_USER:     str = os.getenv("DB_USER",     "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "root")

    # Schémas PostgreSQL
    SCHEMA_SALES:       str = os.getenv("DB_SCHEMA_SALES",      "sales")
    SCHEMA_INVENTORY:   str = os.getenv("DB_SCHEMA_INVENTORY",  "inventory")
    SCHEMA_MONITORING:  str = os.getenv("DB_SCHEMA_MONITORING", "monitoring")

    # DSN complet (psycopg2 + asyncpg)
    @classmethod
    def dsn_sync(cls) -> str:
        return (
            f"host={cls.DB_HOST} port={cls.DB_PORT} "
            f"dbname={cls.DB_NAME} user={cls.DB_USER} "
            f"password={cls.DB_PASSWORD}"
        )

    @classmethod
    def dsn_async(cls) -> str:
        return (
            f"postgresql://{cls.DB_USER}:{cls.DB_PASSWORD}"
            f"@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}"
        )

    # ── Ollama LLM ─────────────────────────────────────────────
    OLLAMA_BASE_URL:    str = os.getenv("OLLAMA_BASE_URL",       "http://localhost:11434")
    OLLAMA_MODEL_ANALYST:  str = os.getenv("OLLAMA_MODEL_ANALYST",  "llama3.2:latest")
    OLLAMA_MODEL_STRATEGE: str = os.getenv("OLLAMA_MODEL_STRATEGE", "llama3.2:latest")
    OLLAMA_MODEL_COACH:    str = os.getenv("OLLAMA_MODEL_COACH",    "qwen2.5:0.5b")
    OLLAMA_EMBED_MODEL:    str = os.getenv("OLLAMA_EMBED_MODEL",    "nomic-embed-text")

    # Rétrocompatibilité
    @classmethod
    def ollama_model(cls) -> str:
        return cls.OLLAMA_MODEL_ANALYST

    # ── Milvus RAG ─────────────────────────────────────────────
    MILVUS_URI:        str = os.getenv("MILVUS_URI",        "http://localhost:19530")
    MILVUS_COLLECTION: str = os.getenv("MILVUS_COLLECTION", "coaching_scripts")
    MILVUS_DIM:        int = int(os.getenv("MILVUS_DIM",    "768"))

    # ── Business ───────────────────────────────────────────────
    DEFAULT_STORE_ID:       str   = os.getenv("DEFAULT_STORE_ID",       "I63")
    DEFAULT_STORE_INTERNAL: str   = os.getenv("DEFAULT_STORE_INTERNAL", "OOR_LAC_01")
    DEFAULT_DAILY_TARGET:   float = float(os.getenv("DEFAULT_DAILY_TARGET", "1007.0"))

    URGENCY_HIGH_THRESHOLD:   float = float(os.getenv("URGENCY_HIGH_THRESHOLD",   "0.30"))
    URGENCY_MEDIUM_THRESHOLD: float = float(os.getenv("URGENCY_MEDIUM_THRESHOLD", "0.15"))

    CYCLE_INTERVAL_MINUTES:        int = int(os.getenv("CYCLE_INTERVAL_MINUTES",        "15"))
    REALTIME_TX_INTERVAL_SECONDS:  int = int(os.getenv("REALTIME_TX_INTERVAL_SECONDS",  "15"))

    # ── MLflow / Langfuse ──────────────────────────────────────
    MLFLOW_TRACKING_URI: str = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    LANGFUSE_HOST:       str = os.getenv("LANGFUSE_HOST",       "http://localhost:3001")
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")

    # ── App ────────────────────────────────────────────────────
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL:   str = os.getenv("LOG_LEVEL",   "INFO")
    DEBUG:       bool = os.getenv("DEBUG", "false").lower() == "true"


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()


# Instance globale — import direct possible
config = Config()

# Boutique par défaut — SEULE source autorisée ; ne jamais hardcoder 'I63'
# ailleurs dans le code (voir .env : DEFAULT_STORE_ID).
DEFAULT_STORE_ID: str = config.DEFAULT_STORE_ID
