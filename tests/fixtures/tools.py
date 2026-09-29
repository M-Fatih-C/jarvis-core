"""Test fixtures and mock tools for unit and integration testing."""

from typing import Any
import pytest
from pydantic import BaseModel, Field
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall, ToolDefinition, ToolResult
from core.policy.engine import PolicyEngine
from core.tools.base import JarvisTool
from core.tools.mock import (
    CreateEventMockTool,
    CreateReminderMockTool,
    ListEventsMockTool,
    MemorySearchMockTool,
    SystemStatusMockTool,
    register_mock_tools,
)
from core.tools.registry import ToolRegistry


class DummyInput(BaseModel):
    query: str = Field(default="test")


class DummySensitiveTool(JarvisTool):
    """Tool with R5_SENSITIVE risk level for testing security denial."""

    definition = ToolDefinition(
        name="security.export_passwords",
        description="Extract sensitive saved credentials.",
        risk_level=RiskLevel.R5_SENSITIVE,
        input_schema=DummyInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = DummyInput

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            success=False,
            error="Unauthorized sensitive access",
        )


@pytest.fixture
def populated_tool_registry() -> ToolRegistry:
    """Fixture providing a fresh registry populated with standard mock tools."""
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


@pytest.fixture
def policy_engine() -> PolicyEngine:
    """Fixture providing standard PolicyEngine."""
    return PolicyEngine()
