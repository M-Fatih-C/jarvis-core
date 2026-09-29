"""Unit tests for the Tool Registry."""

import pytest
from core.agent.exceptions import ToolNotFoundError
from core.tools.mock.calendar import ListEventsMockTool
from core.tools.mock.system import SystemStatusMockTool
from core.tools.registry import ToolRegistry


def test_tool_can_register() -> None:
    """Tool can register and be retrieved."""
    registry = ToolRegistry()
    tool = SystemStatusMockTool()
    registry.register(tool)

    retrieved = registry.get("system.get_status")
    assert retrieved is tool
    assert len(registry.list()) == 1
    assert len(registry.list_definitions()) == 1


def test_duplicate_tool_rejected() -> None:
    """Duplicate tool name registration raises ValueError."""
    registry = ToolRegistry()
    tool1 = SystemStatusMockTool()
    tool2 = SystemStatusMockTool()

    registry.register(tool1)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool2)


def test_unknown_tool_error() -> None:
    """Requesting an unknown tool raises ToolNotFoundError."""
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError, match="not found in registry"):
        registry.get("non_existent.tool")


def test_unregister_tool() -> None:
    """Unregistering a tool removes it from registry."""
    registry = ToolRegistry()
    tool = ListEventsMockTool()
    registry.register(tool)

    assert "calendar.list_events" in [t.definition.name for t in registry.list()]
    registry.unregister("calendar.list_events")

    with pytest.raises(ToolNotFoundError):
        registry.get("calendar.list_events")
