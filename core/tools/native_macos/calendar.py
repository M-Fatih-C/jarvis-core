"""Native macOS Calendar tools communicating with JarvisMacAgent via IPC."""

from datetime import datetime, timezone
import time
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from core.logging.setup import get_logger
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool
from integrations.macos.client import MacBridgeClient
from integrations.macos.exceptions import MacBridgeError, PermissionDeniedBridgeError

logger = get_logger("jarvis.tools.native_calendar")


class ListCalendarsInput(BaseModel):
    """Input for calendar.list_calendars."""
    pass


class ListCalendarsTool(JarvisTool):
    """Tool to list available Apple Calendars."""

    definition = ToolDefinition(
        name="calendar.list_calendars",
        description="List all available Apple Calendar accounts and calendars with their IDs and titles.",
        risk_level=RiskLevel.R0_READ,
        input_schema=ListCalendarsInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = ListCalendarsInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.monotonic()
        try:
            res = await self._bridge.call("calendar.list_calendars", {})
            cals = res.get("calendars", [])
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info("calendar_list_calendars_success", count=len(cals), duration_ms=duration_ms)
            return ToolResult(tool_call_id="", success=True, data={"calendars": cals})
        except PermissionDeniedBridgeError as p_err:
            logger.warning("calendar_list_calendars_permission_denied", error=str(p_err))
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            logger.error("calendar_list_calendars_failed", error=str(exc))
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class ListEventsInput(BaseModel):
    """Input for calendar.list_events."""
    start: datetime = Field(description="Start time of the search range (ISO 8601 with timezone)")
    end: datetime = Field(description="End time of the search range (ISO 8601 with timezone)")
    calendar_ids: list[str] | None = Field(default=None, description="Optional list of specific calendar IDs to filter")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum number of events to return")

    @field_validator("start", "end")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (e.g. 2026-10-01T10:00:00+03:00)")
        return v

    @model_validator(mode="after")
    def validate_range(self) -> "ListEventsInput":
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class ListEventsTool(JarvisTool):
    """Tool to query Apple Calendar events in a bounded time range."""

    definition = ToolDefinition(
        name="calendar.list_events",
        description="List scheduled events from Apple Calendar in a specific timezone-aware time window.",
        risk_level=RiskLevel.R0_READ,
        input_schema=ListEventsInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = ListEventsInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        start_time = time.monotonic()
        try:
            params = {
                "start": validated.start.isoformat(),
                "end": validated.end.isoformat(),
                "calendar_ids": validated.calendar_ids,
                "limit": validated.limit,
            }
            res = await self._bridge.call("calendar.list_events", params)
            events = res.get("events", [])
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info("calendar_list_events_success", count=len(events), duration_ms=duration_ms)
            return ToolResult(tool_call_id="", success=True, data={"events": events})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class GetEventInput(BaseModel):
    """Input for calendar.get_event."""
    event_id: str = Field(description="Unique EventKit event identifier")


class GetEventTool(JarvisTool):
    """Tool to retrieve a single calendar event by ID."""

    definition = ToolDefinition(
        name="calendar.get_event",
        description="Retrieve detailed event metadata for a specific Apple Calendar event.",
        risk_level=RiskLevel.R0_READ,
        input_schema=GetEventInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = GetEventInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            res = await self._bridge.call("calendar.get_event", {"event_id": validated.event_id})
            logger.info("calendar_get_event_success", event_id=validated.event_id)
            return ToolResult(tool_call_id="", success=True, data={"event": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class CreateEventInput(BaseModel):
    """Input for calendar.create_event."""
    title: str = Field(min_length=1, max_length=200, description="Title of the calendar event")
    start: datetime = Field(description="Event start time (ISO 8601 with timezone)")
    end: datetime = Field(description="Event end time (ISO 8601 with timezone)")
    calendar_id: str | None = Field(default=None, description="Optional target calendar identifier")
    notes: str | None = Field(default=None, description="Optional description or notes for the event")
    location: str | None = Field(default=None, description="Optional physical or virtual location")
    alarm_minutes_before: int | None = Field(default=None, ge=0, description="Optional alert reminder in minutes prior to start")

    @field_validator("start", "end")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (e.g. 2026-10-01T10:00:00+03:00)")
        return v

    @model_validator(mode="after")
    def validate_duration(self) -> "CreateEventInput":
        if self.end <= self.start:
            raise ValueError("end must be strictly greater than start")
        duration_days = (self.end - self.start).total_seconds() / 86400.0
        if duration_days > 31.0:
            raise ValueError("event duration exceeds maximum limit of 31 days")
        return self


class CreateEventTool(JarvisTool):
    """Tool to create a verified event in Apple Calendar (R2 Write)."""

    definition = ToolDefinition(
        name="calendar.create_event",
        description="Create a new event in Apple Calendar. Requires human approval before write.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=CreateEventInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = CreateEventInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            params = {
                "title": validated.title,
                "start": validated.start.isoformat(),
                "end": validated.end.isoformat(),
                "calendar_id": validated.calendar_id,
                "notes": validated.notes,
                "location": validated.location,
                "alarm_minutes_before": validated.alarm_minutes_before,
            }
            res = await self._bridge.call("calendar.create_event", params)
            logger.info("calendar_create_event_success", event_id=res.get("id"))
            return ToolResult(tool_call_id="", success=True, data={"status": "created", "event": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class UpdateEventInput(BaseModel):
    """Input for calendar.update_event."""
    event_id: str = Field(description="Unique EventKit event identifier to modify")
    recurrence_scope: Literal["THIS_OCCURRENCE", "FUTURE_OCCURRENCES"] | None = Field(
        default=None,
        description="Explicit recurrence scope required if modifying a recurring event series",
    )
    title: str | None = Field(default=None, description="New title")
    start: datetime | None = Field(default=None, description="New start time (ISO 8601 with timezone)")
    end: datetime | None = Field(default=None, description="New end time (ISO 8601 with timezone)")
    notes: str | None = Field(default=None, description="New notes")
    location: str | None = Field(default=None, description="New location")

    @field_validator("start", "end")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v

    @model_validator(mode="after")
    def validate_range(self) -> "UpdateEventInput":
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("end must be strictly greater than start")
        return self


class UpdateEventTool(JarvisTool):
    """Tool to update an existing Apple Calendar event (R2 Write)."""

    definition = ToolDefinition(
        name="calendar.update_event",
        description="Update an existing Apple Calendar event. Recurring events require recurrence_scope.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=UpdateEventInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = UpdateEventInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            params: dict[str, Any] = {
                "event_id": validated.event_id,
                "recurrence_scope": validated.recurrence_scope,
            }
            if validated.title is not None:
                params["title"] = validated.title
            if validated.start is not None:
                params["start"] = validated.start.isoformat()
            if validated.end is not None:
                params["end"] = validated.end.isoformat()
            if validated.notes is not None:
                params["notes"] = validated.notes
            if validated.location is not None:
                params["location"] = validated.location

            res = await self._bridge.call("calendar.update_event", params)
            logger.info("calendar_update_event_success", event_id=validated.event_id)
            return ToolResult(tool_call_id="", success=True, data={"status": "updated", "event": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class DeleteEventInput(BaseModel):
    """Input for calendar.delete_event."""
    event_id: str = Field(description="Unique EventKit event identifier to delete")
    recurrence_scope: Literal["THIS_OCCURRENCE", "FUTURE_OCCURRENCES"] | None = Field(
        default=None,
        description="Explicit recurrence scope required if deleting an occurrence or series",
    )


class DeleteEventTool(JarvisTool):
    """Tool to delete an Apple Calendar event (R4 Destructive)."""

    definition = ToolDefinition(
        name="calendar.delete_event",
        description="Permanently delete a calendar event from Apple Calendar. High-risk destructive action.",
        risk_level=RiskLevel.R4_DESTRUCTIVE,
        input_schema=DeleteEventInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = DeleteEventInput

    def __init__(self, bridge_client: MacBridgeClient) -> None:
        self._bridge = bridge_client

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        try:
            params = {
                "event_id": validated.event_id,
                "recurrence_scope": validated.recurrence_scope,
            }
            res = await self._bridge.call("calendar.delete_event", params)
            logger.info("calendar_delete_event_success", event_id=validated.event_id)
            return ToolResult(tool_call_id="", success=True, data={"status": "deleted", "result": res})
        except PermissionDeniedBridgeError as p_err:
            return ToolResult(tool_call_id="", success=False, error=f"PERMISSION_DENIED: {p_err}")
        except MacBridgeError as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))
