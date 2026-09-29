"""Domain models for Jarvis tools, definitions, calls, and results."""

from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(IntEnum):
    """Deterministic risk classification for tools."""
    R0_READ = 0          # Pure read-only queries (e.g. status, list)
    R1_LOCAL_LOW = 1     # Low impact local non-destructive operations
    R2_WRITE = 2         # Modifying state (creating events, reminders)
    R3_EXTERNAL = 3      # Outbound requests / external interactions
    R4_DESTRUCTIVE = 4   # Deletions, overwrites, system state changes
    R5_SENSITIVE = 5     # Credential access, security bypass (always denied)


class ToolDefinition(BaseModel):
    """Specification of a tool exposed to the agent and policy engine."""
    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Unique tool identifier in namespace.action format")
    description: str = Field(description="Human- and LLM-readable summary of tool purpose")
    risk_level: RiskLevel = Field(description="Risk classification for policy checks")
    input_schema: dict[str, Any] = Field(description="JSON schema representing tool input parameters")
    requires_approval: bool = Field(
        default=False,
        description="Whether this tool inherently requires human approval regardless of base policy"
    )


class ToolCall(BaseModel):
    """An action proposed by the LLM to invoke a specific tool."""
    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique invocation ID (e.g. call_...)")
    name: str = Field(description="Target tool name")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Parsed tool input arguments")
    requested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware timestamp when the call was proposed"
    )

    @field_validator("requested_at")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class ToolResult(BaseModel):
    """Output from executing a tool."""
    model_config = ConfigDict(frozen=True)

    tool_call_id: str = Field(description="Matching ToolCall ID")
    success: bool = Field(description="Whether execution succeeded")
    data: dict[str, Any] | list[Any] | str | None = Field(default=None, description="Structured result data")
    error: str | None = Field(default=None, description="Error message if execution failed")
