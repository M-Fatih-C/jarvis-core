"""Agent domain models including mode and run representation."""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, field_validator
from core.agent.state_machine import AgentState
from core.models.tools import ToolCall


class AgentMode(str, Enum):
    """Operating mode of the agent."""
    OBSERVE = "observe"
    ASSIST = "assist"
    AUTONOMOUS = "autonomous"


class AgentRun(BaseModel):
    """Stateful record of an agent execution lifecycle."""
    id: UUID = Field(default_factory=uuid4, description="Unique execution run identifier")
    state: AgentState = Field(default=AgentState.CREATED, description="Current lifecycle state")
    source: str = Field(default="chat", description="Origin of request (chat, script, etc.)")
    agent_mode: AgentMode = Field(default=AgentMode.ASSIST, description="Operational policy mode")
    user_input: str = Field(description="Initial prompt or message from user")

    user_id: str | None = None
    conversation_id: str | None = None

    step_count: int = Field(default=0, description="Number of cognitive loops completed")
    tool_call_count: int = Field(default=0, description="Total number of tools executed")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Run initialization timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last state update timestamp"
    )

    final_response: str | None = Field(default=None, description="Final user-facing response text")
    error: str | None = Field(default=None, description="Error message if run failed")

    # In-memory approval linkages
    pending_approval_id: UUID | None = Field(default=None, description="ID of active approval request")
    pending_tool_call: ToolCall | None = Field(default=None, description="ToolCall awaiting approval resolution")

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v
