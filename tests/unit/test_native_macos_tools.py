"""Unit tests for native macOS tools, argument schemas, policy mapping, and error handling (Milestone 3, Spec items 8-16, 28)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from core.models.tools import RiskLevel
from core.tools.native_macos.calendar import (
    CreateEventInput,
    CreateEventTool,
    DeleteEventInput,
    DeleteEventTool,
    GetEventTool,
    ListCalendarsTool,
    ListEventsInput,
    ListEventsTool,
    UpdateEventInput,
    UpdateEventTool,
)
from core.tools.native_macos.notifications import (
    CancelNotificationTool,
    NotificationStatusTool,
    ScheduleNotificationTool,
    ShowNotificationTool,
)
from core.tools.native_macos.provider import register_apple_tools
from core.tools.native_macos.reminders import (
    CompleteReminderTool,
    CreateReminderInput,
    CreateReminderTool,
    DeleteReminderTool,
    ListReminderListsTool,
    ListRemindersInput,
    ListRemindersTool,
    UpdateReminderTool,
)
from core.tools.registry import ToolRegistry
from integrations.macos.client import MacBridgeClient
from integrations.macos.exceptions import EventKitBridgeError, PermissionDeniedBridgeError


@pytest.fixture
def mock_bridge():
    bridge = AsyncMock(spec=MacBridgeClient)
    return bridge


def test_calendar_tools_risk_levels(mock_bridge: AsyncMock) -> None:
    assert ListCalendarsTool(mock_bridge).definition.risk_level == RiskLevel.R0_READ
    assert ListCalendarsTool(mock_bridge).definition.requires_approval is False

    assert ListEventsTool(mock_bridge).definition.risk_level == RiskLevel.R0_READ
    assert ListEventsTool(mock_bridge).definition.requires_approval is False

    assert GetEventTool(mock_bridge).definition.risk_level == RiskLevel.R0_READ
    assert GetEventTool(mock_bridge).definition.requires_approval is False

    assert CreateEventTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert CreateEventTool(mock_bridge).definition.requires_approval is True

    assert UpdateEventTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert UpdateEventTool(mock_bridge).definition.requires_approval is True

    assert DeleteEventTool(mock_bridge).definition.risk_level == RiskLevel.R4_DESTRUCTIVE
    assert DeleteEventTool(mock_bridge).definition.requires_approval is True


def test_reminder_tools_risk_levels(mock_bridge: AsyncMock) -> None:
    assert ListReminderListsTool(mock_bridge).definition.risk_level == RiskLevel.R0_READ
    assert ListRemindersTool(mock_bridge).definition.risk_level == RiskLevel.R0_READ
    assert CreateReminderTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert CreateReminderTool(mock_bridge).definition.requires_approval is True

    assert UpdateReminderTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert UpdateReminderTool(mock_bridge).definition.requires_approval is True

    # complete must be R2_WRITE, not R0_READ!
    assert CompleteReminderTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert CompleteReminderTool(mock_bridge).definition.requires_approval is True

    assert DeleteReminderTool(mock_bridge).definition.risk_level == RiskLevel.R4_DESTRUCTIVE
    assert DeleteReminderTool(mock_bridge).definition.requires_approval is True


def test_notification_tools_risk_levels(mock_bridge: AsyncMock) -> None:
    assert NotificationStatusTool(mock_bridge).definition.risk_level == RiskLevel.R0_READ
    assert ShowNotificationTool(mock_bridge).definition.risk_level == RiskLevel.R1_LOCAL_LOW
    assert ScheduleNotificationTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert ScheduleNotificationTool(mock_bridge).definition.requires_approval is True
    assert CancelNotificationTool(mock_bridge).definition.risk_level == RiskLevel.R2_WRITE
    assert CancelNotificationTool(mock_bridge).definition.requires_approval is True


def test_calendar_validation_requires_timezone() -> None:
    # Naive datetime should fail
    with pytest.raises(ValidationError, match="timezone-aware"):
        ListEventsInput(start="2026-10-01T10:00:00", end="2026-10-01T11:00:00")

    # Timezone-aware succeeds
    inp = ListEventsInput(
        start="2026-10-01T10:00:00+03:00",
        end="2026-10-01T11:00:00+03:00",
    )
    assert inp.start.tzinfo is not None

    # End before start fails
    with pytest.raises(ValidationError, match="greater than or equal to start"):
        ListEventsInput(
            start="2026-10-01T12:00:00+03:00",
            end="2026-10-01T10:00:00+03:00",
        )


def test_calendar_create_validation() -> None:
    # End <= start fails
    with pytest.raises(ValidationError, match="strictly greater than start"):
        CreateEventInput(
            title="Meeting",
            start="2026-10-01T10:00:00+03:00",
            end="2026-10-01T10:00:00+03:00",
        )

    # Duration > 31 days fails
    with pytest.raises(ValidationError, match="exceeds maximum limit"):
        CreateEventInput(
            title="Long event",
            start="2026-10-01T10:00:00+03:00",
            end="2026-11-15T10:00:00+03:00",
        )


def test_reminder_create_validation() -> None:
    # Empty title fails
    with pytest.raises(ValidationError):
        CreateReminderInput(title="")

    # Naive due_at fails
    with pytest.raises(ValidationError, match="timezone-aware"):
        CreateReminderInput(title="Valid", due_at="2026-10-01T19:00:00")


@pytest.mark.asyncio
async def test_calendar_list_events_execution(mock_bridge: AsyncMock) -> None:
    mock_bridge.call.return_value = {
        "events": [
            {
                "id": "ev-1",
                "calendar_id": "cal-1",
                "title": "Existing Event",
                "start": "2026-10-01T10:00:00+03:00",
                "end": "2026-10-01T11:00:00+03:00",
                "all_day": False,
            }
        ]
    }

    tool = ListEventsTool(mock_bridge)
    result = await tool.execute({
        "start": "2026-10-01T00:00:00+03:00",
        "end": "2026-10-01T23:59:59+03:00",
    })

    assert result.success is True
    assert len(result.data["events"]) == 1
    mock_bridge.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_denied_graceful_result(mock_bridge: AsyncMock) -> None:
    mock_bridge.call.side_effect = PermissionDeniedBridgeError("Calendar access is denied.")

    tool = ListEventsTool(mock_bridge)
    result = await tool.execute({
        "start": "2026-10-01T00:00:00+03:00",
        "end": "2026-10-01T23:59:59+03:00",
    })

    assert result.success is False
    assert "PERMISSION_DENIED" in result.error


@pytest.mark.asyncio
async def test_reminders_complete_execution(mock_bridge: AsyncMock) -> None:
    mock_bridge.call.return_value = {
        "id": "rem-123",
        "title": "Study YBS",
        "completed": True,
    }

    tool = CompleteReminderTool(mock_bridge)
    result = await tool.execute({
        "reminder_id": "rem-123",
        "completed": True,
    })

    assert result.success is True
    assert result.data["status"] == "completed"
    assert result.data["reminder"]["completed"] is True
    mock_bridge.call.assert_awaited_with("reminders.complete", {
        "reminder_id": "rem-123",
        "completed": True,
    })


def test_provider_registration(mock_bridge: AsyncMock) -> None:
    # 1. Mock provider registers mock tools
    mock_reg = ToolRegistry()
    register_apple_tools(mock_reg, provider="mock")
    assert mock_reg.get("calendar.list_events").definition.description == "List upcoming calendar events."

    # 2. Native macOS provider registers real native tools
    native_reg = ToolRegistry()
    register_apple_tools(native_reg, provider="native_macos", client=mock_bridge)
    assert native_reg.get("calendar.list_events").definition.description == (
        "List scheduled events from Apple Calendar in a specific timezone-aware time window."
    )
    assert native_reg.get("reminders.complete") is not None
    assert native_reg.get("notifications.show") is not None
