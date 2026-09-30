import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from core.models.tools import ToolCall


def compute_action_digest(
    agent_run_id: UUID | str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Compute canonical SHA-256 action digest binding agent run, tool call, and arguments."""
    canonical_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload = f"{agent_run_id}:{tool_call_id}:{tool_name}:{canonical_json}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    action_digest: str | None = Field(
        default=None,
        description="SHA-256 digest binding approval to exact agent run, tool call, and arguments",
    )
    consumed_at: datetime | None = Field(
        default=None,
        description="Timezone-aware timestamp when approved action was consumed/executed",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware timestamp when approval was requested"
    )
    expires_at: datetime = Field(description="Timezone-aware expiration timestamp")

    @field_validator("created_at", "expires_at", "consumed_at")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v

    @model_validator(mode="before")
    @classmethod
    def populate_digest(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("action_digest") and "tool_call" in data and "agent_run_id" in data:
                tc = data["tool_call"]
                tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "")
                tc_name = getattr(tc, "name", None) or (tc.get("name") if isinstance(tc, dict) else "")
                tc_args = getattr(tc, "arguments", None) or (tc.get("arguments") if isinstance(tc, dict) else {})
                data["action_digest"] = compute_action_digest(data["agent_run_id"], tc_id, tc_name, tc_args)
        return data
