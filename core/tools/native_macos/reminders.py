"""Native macOS Reminders tools communicating with JarvisMacAgent via IPC."""

from datetime import datetime
import time
from typing import Any
from pydantic import BaseModel, Field, field_validator
from core.logging.setup import get_logger
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool
from integrations.macos.client import MacBridgeClient
from integrations.macos.exceptions import MacBridgeError, PermissionDeniedBridgeError

logger = get_logger("jarvis.tools.native_reminders")


class ListReminderListsInput(BaseModel):
    """Input for reminders.list_lists."""
    pass


class ListReminderListsTool(JarvisTool):
    """Tool to list Apple Reminder lists."""

    definition = ToolDefinition(
        name="reminders.list_lists",
        description="List all available Apple Reminder lists with their IDs and titles.",
        risk_level=RiskLevel.R0_READ,
        input_schema=ListReminderListsInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = ListReminderListsInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.monotonic()
        try:
            res = await self._bridge.call("reminders.list_lists", {})
            lists = res.get("lists", [])
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info("reminders_list_lists_success", count=len(lists), duration_ms=duration_ms)
            return ToolResult(tool_call_id="", success=True, data={"lists": lists})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class ListRemindersInput(BaseModel):
    """Input for reminders.list."""
    list_id: str | None = Field(default=None, description="Optional reminder list ID to filter")
    completed: bool | None = Field(default=None, description="Optional completion state filter")
    due_before: datetime | None = Field(default=None, description="Filter reminders due on or before this datetime")
    due_after: datetime | None = Field(default=None, description="Filter reminders due on or after this datetime")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum number of reminders to return")

    @field_validator("due_before", "due_after")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (e.g. 2026-10-01T10:00:00+03:00)")
        return v


class ListRemindersTool(JarvisTool):
    """Tool to query Apple Reminders with filters."""

    definition = ToolDefinition(
        name="reminders.list",
        description="Query Apple Reminders with optional list, completion, and due date filters.",
        risk_level=RiskLevel.R0_READ,
        input_schema=ListRemindersInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = ListRemindersInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        start_time = time.monotonic()
        try:
            params: dict[str, Any] = {"limit": validated.limit}
            if validated.list_id:
                params["list_id"] = validated.list_id
            if validated.completed is not None:
                params["completed"] = validated.completed
            if validated.due_before:
                params["due_before"] = validated.due_before.isoformat()
            if validated.due_after:
                params["due_after"] = validated.due_after.isoformat()

            res = await self._bridge.call("reminders.list", params)
            reminders = res.get("reminders", [])
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info("reminders_list_success", count=len(reminders), duration_ms=duration_ms)
            return ToolResult(tool_call_id="", success=True, data={"reminders": reminders})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class CreateReminderInput(BaseModel):
    """Input for reminders.create."""
    title: str = Field(min_length=1, max_length=300, description="Title of the reminder task")
    notes: str | None = Field(default=None, description="Optional extra notes or description")
    due_at: datetime | None = Field(default=None, description="Optional timezone-aware due datetime")
    list_id: str | None = Field(default=None, description="Optional target reminder list ID")
    priority: int | None = Field(default=0, ge=0, le=9, description="Optional priority level (0: none, 1: high, 5: medium, 9: low)")
    alarm: datetime | None = Field(default=None, description="Optional alarm trigger datetime")

    @field_validator("due_at", "alarm")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (e.g. 2026-10-01T19:00:00+03:00)")
        return v


class CreateReminderTool(JarvisTool):
    """Tool to create a reminder task in Apple Reminders (R2 Write)."""

    definition = ToolDefinition(
        name="reminders.create",
        description="Create a new reminder in Apple Reminders. Requires human approval before save.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=CreateReminderInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = CreateReminderInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            params: dict[str, Any] = {
                "title": validated.title,
                "notes": validated.notes,
                "list_id": validated.list_id,
                "priority": validated.priority,
            }
            if validated.due_at is not None:
                params["due_at"] = validated.due_at.isoformat()
            if validated.alarm is not None:
                params["alarm"] = validated.alarm.isoformat()

            res = await self._bridge.call("reminders.create", params)
            reminder_id = res.get("id")
            if not reminder_id:
                return ToolResult(tool_call_id="", success=False, error="EventKit returned no ID; verify before retrying")
            verified = await self._bridge.call("reminders.get", {"reminder_id": reminder_id})
            due_matches = validated.due_at is None or (isinstance(verified.get("due_at"), str)
                and datetime.fromisoformat(verified["due_at"]) == validated.due_at)
            if verified.get("id") != reminder_id or verified.get("title") != validated.title or not due_matches:
                return ToolResult(tool_call_id="", success=False, error="Reminder read-back mismatch; verify before retrying")
            logger.info("reminders_create_success", reminder_id=reminder_id)
            return ToolResult(tool_call_id="", success=True, data={"status": "created", "reminder": verified})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class UpdateReminderInput(BaseModel):
    """Input for reminders.update."""
    reminder_id: str = Field(description="Unique reminder ID to update")
    title: str | None = Field(default=None, description="New title")
    notes: str | None = Field(default=None, description="New notes")
    due_at: datetime | None = Field(default=None, description="New due datetime")
    priority: int | None = Field(default=None, ge=0, le=9, description="New priority")

    @field_validator("due_at")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class UpdateReminderTool(JarvisTool):
    """Tool to update an existing Apple Reminder (R2 Write)."""

    definition = ToolDefinition(
        name="reminders.update",
        description="Update an existing Apple Reminder. Requires human approval before write.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=UpdateReminderInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = UpdateReminderInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            params: dict[str, Any] = {"reminder_id": validated.reminder_id}
            if validated.title is not None:
                params["title"] = validated.title
            if validated.notes is not None:
                params["notes"] = validated.notes
            if validated.due_at is not None:
                params["due_at"] = validated.due_at.isoformat()
            if validated.priority is not None:
                params["priority"] = validated.priority

            res = await self._bridge.call("reminders.update", params)
            logger.info("reminders_update_success", reminder_id=validated.reminder_id)
            return ToolResult(tool_call_id="", success=True, data={"status": "updated", "reminder": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class CompleteReminderInput(BaseModel):
    """Input for reminders.complete."""
    reminder_id: str = Field(description="Unique reminder ID to mark completed or incomplete")
    completed: bool = Field(default=True, description="True to complete task, False to mark incomplete")


class CompleteReminderTool(JarvisTool):
    """Tool to mark an Apple Reminder complete (R2 Write)."""

    definition = ToolDefinition(
        name="reminders.complete",
        description="Mark a reminder as completed. Data mutation requiring human approval.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=CompleteReminderInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = CompleteReminderInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            res = await self._bridge.call("reminders.complete", {
                "reminder_id": validated.reminder_id,
                "completed": validated.completed,
            })
            logger.info("reminders_complete_success", reminder_id=validated.reminder_id, completed=validated.completed)
            return ToolResult(tool_call_id="", success=True, data={"status": "completed", "reminder": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class DeleteReminderInput(BaseModel):
    """Input for reminders.delete."""
    reminder_id: str = Field(description="Unique reminder ID to permanently delete")


class DeleteReminderTool(JarvisTool):
    """Tool to permanently delete an Apple Reminder (R4 Destructive)."""

    definition = ToolDefinition(
        name="reminders.delete",
        description="Permanently delete a reminder task from Apple Reminders. High-risk destructive action.",
        risk_level=RiskLevel.R4_DESTRUCTIVE,
        input_schema=DeleteReminderInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = DeleteReminderInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            res = await self._bridge.call("reminders.delete", {"reminder_id": validated.reminder_id})
            logger.info("reminders_delete_success", reminder_id=validated.reminder_id)
            return ToolResult(tool_call_id="", success=True, data={"status": "deleted", "result": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))
