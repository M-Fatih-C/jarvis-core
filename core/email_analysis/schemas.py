"""Pydantic schemas and enum definitions for intelligent email analysis and task extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EmailCategory(str, Enum):
    """Categorization categories for incoming emails."""

    WORK_CAREER = "work_career"         # Work, career and job applications
    EDUCATION = "education"             # University, education and examinations
    FINANCE = "finance"                 # Banking, payments and financial matters
    MEETINGS = "meetings"               # Meetings and appointments
    PERSONAL = "personal"               # Personal communication
    GENERAL = "general"                 # General information
    PROMOTIONAL = "promotional"         # Promotional or low-priority messages


class ImportanceLevel(str, Enum):
    """3-tier importance classification."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class TaskPriority(str, Enum):
    """Priority level for extracted task proposals."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DeadlineConfidence(str, Enum):
    """Confidence level in deadline temporal grounding."""

    EXACT = "exact"         # Precise date and time provided (e.g. "October 8, 2026 at 17:00")
    INFERRED = "inferred"   # Relative anchor resolved with high certainty (e.g. "tomorrow 19:00")
    UNCERTAIN = "uncertain" # Vague or ambiguous date expression (e.g. "by end of week")
    NONE = "none"           # No deadline mentioned


class EmailAnalysisResult(BaseModel):
    """Structured result of local AI email analysis."""

    category: EmailCategory
    importance: ImportanceLevel
    requires_response: bool = False
    contains_task: bool = False
    has_deadline: bool = False
    has_meeting: bool = False
    security_or_payment: bool = False
    summary: str = Field(description="A concise summary of the email content")
    proposed_action: str = Field(description="Proposed next action for the user")
    
    # Task extraction details (populated when contains_task is True)
    extracted_task_title: str | None = None
    extracted_task_description: str | None = None
    raw_deadline_text: str | None = None
    parsed_deadline: datetime | None = None
    deadline_confidence: DeadlineConfidence = DeadlineConfidence.NONE


class TaskProposal(BaseModel):
    """Structured task proposal extracted from an email."""

    task_id: UUID = Field(default_factory=uuid4)
    source_message_id: str
    title: str
    description: str
    category: EmailCategory
    priority: TaskPriority
    deadline: datetime | None = None
    deadline_confidence: DeadlineConfidence = DeadlineConfidence.NONE
    raw_deadline_text: str | None = None
    proposed_action: str = "Review email"
    status: Literal["proposed", "dismissed", "approved", "completed"] = "proposed"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
