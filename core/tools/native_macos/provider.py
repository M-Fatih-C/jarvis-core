"""Tool provider registration supporting mock vs native macOS EventKit tools."""

import sys
from core.logging.setup import get_logger
from core.tools.mock import register_mock_tools
from core.tools.native_macos.calendar import (
    CreateEventTool,
    DeleteEventTool,
    GetEventTool,
    ListCalendarsTool,
    ListEventsTool,
    UpdateEventTool,
)
from core.tools.native_macos.notifications import (
    CancelNotificationTool,
    NotificationStatusTool,
    ScheduleNotificationTool,
    ShowNotificationTool,
)
from core.tools.native_macos.reminders import (
    CompleteReminderTool,
    CreateReminderTool,
    DeleteReminderTool,
    ListReminderListsTool,
    ListRemindersTool,
    UpdateReminderTool,
)
from core.tools.registry import ToolRegistry
from integrations.macos.client import MacBridgeClient

logger = get_logger("jarvis.tools.provider")


def register_apple_tools(
    registry: ToolRegistry,
    provider: str = "mock",
    client: MacBridgeClient | None = None,
) -> None:
    """Register Apple productivity tools according to the chosen provider.
    
    Args:
        registry: The target ToolRegistry instance.
        provider: 'mock' or 'native_macos'.
        client: Optional MacBridgeClient instance for native execution.
    """
    if provider == "native_macos":
        bridge_client = client or MacBridgeClient()

        # Calendar tools
        registry.register(ListCalendarsTool(bridge_client))
        registry.register(ListEventsTool(bridge_client))
        registry.register(GetEventTool(bridge_client))
        registry.register(CreateEventTool(bridge_client))
        registry.register(UpdateEventTool(bridge_client))
        registry.register(DeleteEventTool(bridge_client))

        # Reminder tools
        registry.register(ListReminderListsTool(bridge_client))
        registry.register(ListRemindersTool(bridge_client))
        registry.register(CreateReminderTool(bridge_client))
        registry.register(UpdateReminderTool(bridge_client))
        registry.register(CompleteReminderTool(bridge_client))
        registry.register(DeleteReminderTool(bridge_client))

        # Notification tools
        registry.register(NotificationStatusTool(bridge_client))
        registry.register(ShowNotificationTool(bridge_client))
        registry.register(ScheduleNotificationTool(bridge_client))
        registry.register(CancelNotificationTool(bridge_client))

        logger.info("native_macos_tools_registered")
    else:
        register_mock_tools(registry)
        logger.info("mock_apple_tools_registered")
