"""Jarvis Configuration Management via Pydantic Settings."""

from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application
    app_name: str = "Jarvis Core"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8765

    # LLM Settings
    model_id: str = "mlx-community/Qwen3.5-4B-MLX-4bit"
    inference_profile: Literal["fast", "deep"] = "fast"

    # Agent Runtime Limits
    max_agent_steps: int = 12
    max_tool_calls: int = 8
    max_retries: int = 2
    default_agent_mode: Literal["observe", "assist", "autonomous"] = "assist"
    default_timezone: str = "Europe/Istanbul"
    approval_ttl_seconds: int = 300

    # Observability
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
