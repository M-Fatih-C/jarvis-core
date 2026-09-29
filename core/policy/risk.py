"""Risk policy models and decision types for Jarvis."""

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from core.models.agent import AgentMode
from core.models.tools import ToolCall, ToolDefinition


class PolicyDecisionType(str, Enum):
    """Possible outcomes of a policy evaluation."""
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyDecision(BaseModel):
    """The deterministic verdict returned by the Policy Engine."""
    model_config = ConfigDict(frozen=True)

    decision: PolicyDecisionType
    reason: str


class PolicyRequest(BaseModel):
    """Encapsulates context required for policy decision."""
    model_config = ConfigDict(frozen=True)

    agent_mode: AgentMode
    tool_definition: ToolDefinition
    tool_call: ToolCall
