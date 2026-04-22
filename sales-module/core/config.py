from pydantic_settings import BaseSettings
from functools import lru_cache


class DatabaseConfig(BaseSettings):
    database_url:      str = "postgresql+asyncpg://asc_user:asc_password@localhost:5432/asc_db"
    postgres_host:     str = "localhost"
    postgres_port:     int = 5432
    postgres_db:       str = "asc_db"
    postgres_user:     str = "asc_user"
    postgres_password: str = "asc_password"


class RedisConfig(BaseSettings):
    redis_url: str = "redis://localhost:6379"


class MilvusConfig(BaseSettings):
    milvus_host: str = "localhost"
    milvus_port: int = 19530


class KafkaConfig(BaseSettings):
    kafka_brokers:  str = "localhost:9092"
    kafka_group_id: str = "asc-agents"

    # Topics
    topic_pos:         str = "pos.transactions"
    topic_wms:         str = "wms.stock"
    topic_weather:     str = "context.weather"
    topic_cycles:      str = "agent.cycles"
    topic_feedback:    str = "agent.feedback"


class MLConfig(BaseSettings):
    vllm_url:          str = "http://localhost:8080"
    vllm_model:        str = "mistralai/Mistral-7B-Instruct-v0.3"
    timefm_model_path: str = "./models/timefm-1.0-200m"
    timefm_horizon:    int = 8
    timefm_mape_max:   float = 0.20


class ObservabilityConfig(BaseSettings):
    langfuse_public_key: str = "pk-lf-local"
    langfuse_secret_key: str = "sk-lf-local"
    langfuse_host:       str = "http://localhost:3001"
    mlflow_tracking_uri: str = "http://localhost:5000"
    drift_threshold:     float = 0.15


class Settings(
    DatabaseConfig,
    RedisConfig,
    MilvusConfig,
    KafkaConfig,
    MLConfig,
    ObservabilityConfig
):
    app_env:    str = "development"
    app_port:   int = 8000
    secret_key: str = "your-secret-key-here"

    class Config:
        env_file = ".env"
        extra    = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()