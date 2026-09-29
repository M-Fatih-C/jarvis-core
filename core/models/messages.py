"""Chat message models for Jarvis agent conversations."""

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from core.models.tools import ToolCall


class MessageRole(str, Enum):
    """Roles for messages within an agent conversation."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """Individual message in an agent context history."""
    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str = Field(default="", description="Text content of the message")
    tool_call_id: str | None = Field(default=None, description="Associated tool call ID if role is TOOL")
    tool_name: str | None = Field(default=None, description="Tool name if role is TOOL")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="Tool calls proposed if role is ASSISTANT")
    is_internal: bool = Field(
        default=False,
        description="Flag indicating synthetic internal system instruction, excluded from user conversation history",
    )
