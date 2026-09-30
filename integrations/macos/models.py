"""Data models for Unix Domain Socket IPC communication with JarvisMacAgent."""

from typing import Any
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field


CURRENT_PROTOCOL_VERSION = 1


class RPCRequest(BaseModel):
    """Outbound RPC request from Python to JarvisMacAgent."""
    model_config = ConfigDict(frozen=True)

    protocol_version: int = Field(default=CURRENT_PROTOCOL_VERSION, description="IPC protocol version")
    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique request ID")
    method: str = Field(description="Target RPC method")
    params: dict[str, Any] = Field(default_factory=dict, description="Method parameter dictionary")


class RPCError(BaseModel):
    """Structured error payload returned from native Swift agent."""
    model_config = ConfigDict(frozen=True)

    code: str = Field(description="Standard error code string (e.g. PERMISSION_DENIED, NOT_FOUND)")
    message: str = Field(description="Human readable error description")
    details: dict[str, Any] | None = Field(default=None, description="Optional diagnostic details")


class RPCResponse(BaseModel):
    """Inbound RPC response from JarvisMacAgent to Python."""
    model_config = ConfigDict(frozen=True)

    protocol_version: int = Field(description="IPC protocol version of response")
    id: str = Field(description="Correlated request ID")
    ok: bool = Field(description="True if native operation succeeded")
    result: Any = Field(default=None, description="Result payload if ok is True")
    error: RPCError | None = Field(default=None, description="Error payload if ok is False")


class CalendarEventDTO(BaseModel):
    """Normalized calendar event representation."""
    id: str
    calendar_id: str
    title: str
    start: str
    end: str
    all_day: bool = False
    location: str | None = None
    notes: str | None = None
    availability: str = "busy"


class ReminderDTO(BaseModel):
    """Normalized reminder representation."""
    id: str
    list_id: str
    title: str
    notes: str | None = None
    completed: bool = False
    due_at: str | None = None
    priority: int = 0


class MacAgentHealthDTO(BaseModel):
    """Health check outcome from native agent."""
    agent: str = "ready"
    protocol_version: int = CURRENT_PROTOCOL_VERSION
    calendar_permission: str
    reminders_permission: str
    notification_permission: str
