"""
aria/config.py
──────────────
Application-wide settings loaded from environment variables (or a .env file).

Access the singleton anywhere in the codebase:

    from aria.config import settings

Never hardcode RISK_LOW_THRESHOLD or RISK_HIGH_THRESHOLD in business logic —
always use settings.risk_low_threshold / settings.risk_high_threshold.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Google Gemini ─────────────────────────────────────────────────────────
    gemini_api_key: str = Field(
        default="",
        alias="GEMINI_API_KEY",
    )

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        alias="KAFKA_BOOTSTRAP_SERVERS",
    )

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    chroma_persist_dir: str = Field(
        default="./chroma_data",
        alias="CHROMA_PERSIST_DIR",
    )

    # ── Risk Thresholds ───────────────────────────────────────────────────────
    # Scores ≤ low  → LOW
    # low < score < high → MEDIUM
    # Scores ≥ high → HIGH
    risk_low_threshold: int = Field(
        default=35,
        alias="RISK_LOW_THRESHOLD",
        ge=0,
        le=100,
    )
    risk_high_threshold: int = Field(
        default=70,
        alias="RISK_HIGH_THRESHOLD",
        ge=0,
        le=100,
    )

    # ── Human-in-the-loop webhook ─────────────────────────────────────────────
    approval_webhook_url: str = Field(
        default="",
        alias="APPROVAL_WEBHOOK_URL",
    )

    # ── Server Ports ──────────────────────────────────────────────────────────
    grpc_port: int = Field(default=50051, alias="GRPC_PORT")
    api_port: int = Field(default=8000, alias="API_PORT")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton. Reads .env exactly once."""
    return Settings()


# Module-level singleton — import this everywhere:
#   from aria.config import settings
settings: Settings = get_settings()
