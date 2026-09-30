"""Native macOS Notifications tools communicating with JarvisMacAgent via IPC."""

from datetime import datetime
import time
from typing import Any
from pydantic import BaseModel, Field, field_validator
from core.logging.setup import get_logger
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool
from integrations.macos.client import MacBridgeClient
from integrations.macos.exceptions import MacBridgeError, PermissionDeniedBridgeError

logger = get_logger("jarvis.tools.native_notifications")


class NotificationStatusInput(BaseModel):
    """Input for notifications.status."""
    pass


class NotificationStatusTool(JarvisTool):
    """Tool to check notification permissions."""

    definition = ToolDefinition(
        name="notifications.status",
        description="Check whether macOS local notifications are authorized or enabled.",
        risk_level=RiskLevel.R0_READ,
        input_schema=NotificationStatusInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = NotificationStatusInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            res = await self._bridge.call("notifications.status", {})
            return ToolResult(tool_call_id="", success=True, data=res)
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class ShowNotificationInput(BaseModel):
    """Input for notifications.show."""
    title: str = Field(min_length=1, max_length=150, description="Title of the notification banner")
    body: str = Field(min_length=1, max_length=500, description="Body text of the notification")
    identifier: str | None = Field(default=None, description="Optional unique identifier for notification deduplication")


class ShowNotificationTool(JarvisTool):
    """Tool to display an immediate local notification (R1 Local Low)."""

    definition = ToolDefinition(
        name="notifications.show",
        description="Display an immediate local macOS notification banner to the user.",
        risk_level=RiskLevel.R1_LOCAL_LOW,
        input_schema=ShowNotificationInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = ShowNotificationInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            res = await self._bridge.call("notifications.show", {
                "title": validated.title,
                "body": validated.body,
                "identifier": validated.identifier,
            })
            logger.info("notification_show_success", identifier=res.get("identifier"))
            return ToolResult(tool_call_id="", success=True, data={"status": "shown", "notification": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class ScheduleNotificationInput(BaseModel):
    """Input for notifications.schedule."""
    title: str = Field(min_length=1, max_length=150, description="Title of the notification")
    body: str = Field(min_length=1, max_length=500, description="Body text of the notification")
    delay_seconds: float | None = Field(default=None, ge=1.0, description="Delay in seconds before triggering")
    scheduled_at: datetime | None = Field(default=None, description="Future timestamp when notification triggers")
    identifier: str | None = Field(default=None, description="Optional unique notification identifier")

    @field_validator("scheduled_at")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class ScheduleNotificationTool(JarvisTool):
    """Tool to schedule a future local notification (R2 Write)."""

    definition = ToolDefinition(
        name="notifications.schedule",
        description="Schedule a local macOS notification banner for a future time. Requires approval.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=ScheduleNotificationInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = ScheduleNotificationInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            params: dict[str, Any] = {
                "title": validated.title,
                "body": validated.body,
                "identifier": validated.identifier,
            }
            if validated.delay_seconds is not None:
                params["delay_seconds"] = validated.delay_seconds
            if validated.scheduled_at is not None:
                params["scheduled_at"] = validated.scheduled_at.isoformat()

            res = await self._bridge.call("notifications.schedule", params)
            logger.info("notification_schedule_success", identifier=res.get("identifier"))
            return ToolResult(tool_call_id="", success=True, data={"status": "scheduled", "notification": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class CancelNotificationInput(BaseModel):
    """Input for notifications.cancel."""
    identifier: str = Field(description="Unique identifier of scheduled notification to cancel")


class CancelNotificationTool(JarvisTool):
    """Tool to cancel a scheduled notification (R2 Write)."""

    definition = ToolDefinition(
        name="notifications.cancel",
        description="Cancel a scheduled local macOS notification by identifier. Requires approval.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=CancelNotificationInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = CancelNotificationInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            res = await self._bridge.call("notifications.cancel", {"identifier": validated.identifier})
            logger.info("notification_cancel_success", identifier=validated.identifier)
            return ToolResult(tool_call_id="", success=True, data={"status": "cancelled", "result": res})
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))
