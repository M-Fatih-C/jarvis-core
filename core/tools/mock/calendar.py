"""Mock calendar tools for listing and creating events."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool


class ListEventsInput(BaseModel):
    """Input parameters for calendar.list_events."""
    limit: int = Field(default=10, description="Maximum number of events to return")


class CreateEventInput(BaseModel):
    """Input parameters for calendar.create_event."""
    title: str = Field(description="Title of the calendar event")
    start: datetime = Field(description="Event start time (ISO 8601 format)")
    end: datetime = Field(description="Event end time (ISO 8601 format)")


class ListEventsMockTool(JarvisTool):
    """Mock tool to list calendar events."""

    definition = ToolDefinition(
        name="calendar.list_events",
        description="List upcoming calendar events.",
        risk_level=RiskLevel.R0_READ,
        input_schema=ListEventsInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = ListEventsInput

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        return ToolResult(
            tool_call_id="",  # Injected by executor
            success=True,
            data={
                "events": [
                    {
                        "title": "Test Meeting",
                        "start": "2026-10-01T10:00:00+03:00",
                    }
                ]
            },
        )


class CreateEventMockTool(JarvisTool):
    """Mock tool to create a calendar event."""

    definition = ToolDefinition(
        name="calendar.create_event",
        description="Create a new calendar event with title, start, and end times.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=CreateEventInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = CreateEventInput

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        return ToolResult(
            tool_call_id="",
            success=True,
            data={
                "status": "created",
                "event": {
                    "title": getattr(validated, "title", arguments.get("title")),
                    "start": str(getattr(validated, "start", arguments.get("start"))),
                    "end": str(getattr(validated, "end", arguments.get("end"))),
                },
            },
        )
