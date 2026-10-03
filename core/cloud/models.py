"""Domain models for cloud synchronization, device registry, and command queues."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from core.models.tools import RiskLevel


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class DeviceRecord(BaseModel):
    """Registered device identity in the user's personal Jarvis fleet."""
    model_config = ConfigDict(frozen=False)

    device_id: str
    type: str  # e.g. "macos", "ios"
    name: str
    status: DeviceStatus = DeviceStatus.ONLINE
    app_version: str | None = "0.1.0"
    health: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=lambda: ["local_llm", "memory", "tool_execution"])
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CommandStatus(str, Enum):
    """Lifecycle states of an asynchronous cloud command."""
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class CloudCommand(BaseModel):
    """A command dispatched via Firestore queue for execution on target device (e.g. Mac)."""
    model_config = ConfigDict(frozen=False)

    id: str
    user_id: str | None = None
    type: str = "tool_execution"
    name: str
    source_device: str
    target_device: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel | None = None
    status: CommandStatus = CommandStatus.QUEUED
    idempotency_key: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    available_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    revision: int = 1
