"""Mock reminders tools for scheduling reminders."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool


class CreateReminderInput(BaseModel):
    """Input parameters for reminders.create."""
    title: str = Field(description="Title or task description of the reminder")
    due_at: datetime = Field(description="Date and time when the reminder is due (ISO 8601 format)")
    notes: str | None = Field(default=None, description="Optional extra notes or context")

    @field_validator("due_at")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("due_at must be timezone-aware (e.g. 2026-09-30T19:00:00+03:00)")
        return v


class CreateReminderMockTool(JarvisTool):
    """Mock tool to create a reminder."""

    definition = ToolDefinition(
        name="reminders.create",
        description="Create a reminder with a title, due datetime, and optional notes.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=CreateReminderInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = CreateReminderInput

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        title = getattr(validated, "title", arguments.get("title"))
        due_at = str(getattr(validated, "due_at", arguments.get("due_at")))
        notes = getattr(validated, "notes", arguments.get("notes"))

        return ToolResult(
            tool_call_id="",
            success=True,
            data={
                "status": "created",
                "message": "Reminder created successfully.",
                "reminder": {
                    "title": title,
                    "due_at": due_at,
                    "notes": notes,
                },
            },
        )
