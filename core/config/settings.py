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

    # Memory Settings (Milestone 2)
    memory_enabled: bool = True
    memory_db_path: str = "~/Library/Application Support/Jarvis/memory.db"
    embedding_provider: Literal["mock", "sentence_transformers"] = "mock"
    embedding_model: str = "intfloat/multilingual-e5-small"
    memory_top_k: int = 8
    max_memory_context_chars: int = 4000
    ranking_weight_semantic: float = 0.55
    ranking_weight_importance: float = 0.20
    ranking_weight_recency: float = 0.15
    ranking_weight_confidence: float = 0.10

    # Firebase & Cloud Settings (Milestone 2)
    firebase_enabled: bool = False
    firebase_project_id: str = "jarvis-local-dev"
    jarvis_uid: str = "default_user"
    firestore_emulator_host: str | None = None
    device_id: str = "mac-mini-main"
    device_name: str = "Jarvis Mac"
    device_type: str = "macos"
    device_heartbeat_interval_seconds: int = 30
    command_poll_interval_seconds: int = 5
    command_lease_duration_seconds: int = 60

    # Apple Native Integration Settings (Milestone 3)
    apple_integration_provider: Literal["mock", "native_macos"] = "mock"
    mac_agent_socket_path: str | None = None

    # Gmail Integration Settings (Milestone 4.1)
    gmail_enabled: bool = False
    gmail_client_id: str | None = None
    gmail_client_secret: str | None = None
    gmail_sync_lookback_days: int = 7
    gmail_max_sync_messages: int = 25
    email_db_path: str = "~/Library/Application Support/Jarvis/email.db"
    email_sync_schedule_enabled: bool = False  # Explicit opt-in required
    email_sync_times: list[str] = ["09:00", "20:00"]

    # Task Planning Settings (Milestone 4.2)
    selected_calendar_id: str | None = None
    default_work_block_duration_minutes: int = 120
    planning_buffer_minutes: int = 15
    planning_work_start_hour: int = 9
    planning_work_end_hour: int = 21


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
