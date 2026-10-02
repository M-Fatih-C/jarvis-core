"""Pydantic schemas and models for intelligent task planning, approval, and execution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from core.email_analysis.schemas import TaskProposal
from core.task_planning.state import TaskProposalStatus


class ActionType(str, Enum):
    """Categorization of proposed actionable items."""

    REMINDER = "reminder"
    CALENDAR_EVENT = "calendar_event"
    WORK_BLOCK = "work_block"


class CalendarCategory(str, Enum):
    """Logical categories for events without requiring separate Apple Calendars."""

    WORK = "WORK"
    EDUCATION = "EDUCATION"
    PERSONAL = "PERSONAL"
    PROJECT = "PROJECT"


class ActionDestination(str, Enum):
    """Target Apple ecosystem application."""

    APPLE_CALENDAR = "apple_calendar"
    APPLE_REMINDERS = "apple_reminders"


class TimeSlotProposal(BaseModel):
    """Calculated conflict-free time slot proposal."""

    start: datetime
    end: datetime
    duration_minutes: int
    score: float = 1.0
    reason: str = "Conflict-free window fitting preferences"


class TaskActionProposal(BaseModel):
    """A concrete planned action for Apple Calendar or Reminders."""

    action_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    source_message_id: str
    action_type: ActionType
    target_destination: ActionDestination
    title: str
    notes: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    due_date: datetime | None = None
    target_calendar_id: str | None = None
    target_list_id: str | None = None
    logical_category: CalendarCategory = CalendarCategory.WORK
    approval_id: UUID | None = None
    action_digest: str | None = None
    status: TaskProposalStatus = TaskProposalStatus.PROPOSED
    external_id: str | None = None  # EventKit event ID or reminder ID
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: datetime | None = None


class CalendarTargetInfo(BaseModel):
    """Metadata describing an Apple Calendar target."""

    id: str
    title: str
    is_writable: bool
    is_icloud: bool
    is_on_my_mac: bool
    source_title: str | None = None
    source_type: str | None = None


class TaskProposalDetailResponse(BaseModel):
    """Detailed view of a task proposal with source email context and actions."""

    task: TaskProposal
    source_email_subject: str | None = None
    source_email_sender: str | None = None
    source_email_date: datetime | None = None
    source_email_preview: str | None = None
    actions: list[TaskActionProposal] = Field(default_factory=list)


class SourceEmailLinkResponse(BaseModel):
    """Bidirectional query response: finding the source email for a Calendar or Reminder record."""

    external_id: str
    action_type: ActionType
    action_title: str
    task_id: UUID
    task_title: str
    source_message_id: str
    email_subject: str
    email_sender: str
    email_sender_email: str
    email_received_at: datetime
    email_preview: str
    executed_at: datetime | None = None
