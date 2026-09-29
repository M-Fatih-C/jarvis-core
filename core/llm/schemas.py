"""Data models and schemas for LLM communication."""

from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from core.models.tools import ToolCall


class InferenceProfile(str, Enum):
    """Inference parameter presets."""
    FAST = "fast"
    DEEP = "deep"


class ProfileConfig(BaseModel):
    """Configuration values for a specific inference profile."""
    temperature: float
    max_tokens: int
    top_p: float = 0.9


# Inference profile presets
PROFILE_SETTINGS: dict[InferenceProfile, ProfileConfig] = {
    InferenceProfile.FAST: ProfileConfig(temperature=0.2, max_tokens=512, top_p=0.8),
    InferenceProfile.DEEP: ProfileConfig(temperature=0.7, max_tokens=2048, top_p=0.95),
}


class LLMResponse(BaseModel):
    """Standardized output returned from an LLM adapter."""
    model_config = ConfigDict(frozen=True)

    content: str | None = Field(default=None, description="Generated assistant message text")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="List of proposed tool calls")
    finish_reason: str | None = Field(default=None, description="Reason for generation stop")
    model: str | None = Field(default=None, description="Model identifier used for generation")
    usage: dict[str, Any] | None = Field(default=None, description="Token usage statistics")
