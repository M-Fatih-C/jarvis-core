"""Models for human-in-the-loop approval workflows."""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.models.tools import ToolCall


class ApprovalStatus(str, Enum):
    """Possible lifecycles of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    """Encapsulates a proposed tool call requiring user confirmation."""
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4, description="Unique approval ID")
    agent_run_id: UUID = Field(description="Associated AgentRun ID")
    tool_call: ToolCall = Field(description="The proposed tool call awaiting approval")
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, description="Current approval status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware timestamp when approval was requested"
    )
    expires_at: datetime = Field(description="Timezone-aware expiration timestamp")

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v
