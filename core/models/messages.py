"""Chat message models for Jarvis agent conversations."""

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


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
    content: str
    tool_call_id: str | None = Field(default=None, description="Associated tool call ID if role is TOOL")
