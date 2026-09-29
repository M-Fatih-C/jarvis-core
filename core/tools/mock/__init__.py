"""Mock tools package for Jarvis Milestone 1."""

from core.tools.mock.calendar import CreateEventMockTool, ListEventsMockTool
from core.tools.mock.memory import MemorySearchMockTool
from core.tools.mock.reminders import CreateReminderMockTool
from core.tools.mock.system import SystemStatusMockTool
from core.tools.registry import ToolRegistry


def register_mock_tools(registry: ToolRegistry) -> None:
    """Register all Milestone 1 mock tools into the provided registry."""
    registry.register(ListEventsMockTool())
    registry.register(CreateEventMockTool())
    registry.register(CreateReminderMockTool())
    registry.register(SystemStatusMockTool())
    registry.register(MemorySearchMockTool())


__all__ = [
    "CreateEventMockTool",
    "CreateReminderMockTool",
    "ListEventsMockTool",
    "MemorySearchMockTool",
    "SystemStatusMockTool",
    "register_mock_tools",
]
