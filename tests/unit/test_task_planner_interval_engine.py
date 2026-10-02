"""Unit tests for the Intelligent Planning Engine: deterministic intervals and deadline separation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from uuid import uuid4

from core.email_analysis.schemas import DeadlineConfidence, EmailCategory, TaskPriority, TaskProposal
from core.task_planning.planner import AmbiguousDeadlineError, TaskPlanner
from core.task_planning.schemas import ActionDestination, ActionType
from integrations.macos.client import MacBridgeClient


def test_calculate_conflict_free_slots_deterministic() -> None:
    """Verify deterministic interval subtraction and safety buffer application."""
    planner = TaskPlanner(buffer_minutes=15)
    tz = timezone.utc

    search_start = datetime(2026, 10, 5, 9, 0, tzinfo=tz)
    search_end = datetime(2026, 10, 5, 21, 0, tzinfo=tz)

    # Existing event: 11:00 to 12:30.
    # With 15 min buffer before & after, busy interval becomes 10:45 to 12:45.
    existing_events = [
        {
            "id": "ev_1",
            "title": "Team Sync",
            "start": "2026-10-05T11:00:00+00:00",
            "end": "2026-10-05T12:30:00+00:00",
        },
        # Second event overlapping or nearby: 14:00 to 15:00
        {
            "id": "ev_2",
            "title": "Architecture Discussion",
            "start": "2026-10-05T14:00:00+00:00",
            "end": "2026-10-05T15:00:00+00:00",
        },
    ]

    slots = planner.calculate_conflict_free_slots(
        search_start=search_start,
        search_end=search_end,
        duration_minutes=120,
        existing_events=existing_events,
        daily_start_hour=9,
        daily_end_hour=21,
    )

    assert len(slots) > 0
    for s in slots:
        assert (s.end - s.start).total_seconds() == 7200  # Exactly 120 minutes

        # Verify no slot overlaps with 10:45 to 12:45
        assert not (s.start < datetime(2026, 10, 5, 12, 45, tzinfo=tz) and s.end > datetime(2026, 10, 5, 10, 45, tzinfo=tz))

        # Verify no slot overlaps with 13:45 to 15:15
        assert not (s.start < datetime(2026, 10, 5, 15, 15, tzinfo=tz) and s.end > datetime(2026, 10, 5, 13, 45, tzinfo=tz))


def test_slots_match_memory_evening_preference() -> None:
    """Verify slots in user's preferred evening hours receive higher preference score."""
    planner = TaskPlanner(buffer_minutes=15)
    tz = timezone.utc

    search_start = datetime(2026, 10, 6, 9, 0, tzinfo=tz)
    search_end = datetime(2026, 10, 6, 21, 0, tzinfo=tz)

    slots = planner.calculate_conflict_free_slots(
        search_start=search_start,
        search_end=search_end,
        duration_minutes=120,
        existing_events=[],
        daily_start_hour=9,
        daily_end_hour=21,
        preferred_hours=(19, 22),
        preferred_reason="Kullanıcı hafıza tercihi: Akşam çalışma bloğu",
    )

    assert len(slots) > 0
    top_slot = slots[0]
    # Highest ranked slot should match the evening preference
    assert top_slot.start.hour >= 18
    assert top_slot.score > 1.0
    assert "Akşam" in top_slot.reason


def test_ambiguous_deadline_requires_user_date() -> None:
    """Verify that an ambiguous deadline does not hallucinate a date and raises AmbiguousDeadlineError."""
    planner = TaskPlanner()
    vague_proposal = TaskProposal(
        source_message_id="msg_vague_1",
        title="Dilekçe teslimi",
        description="Fakülteye teslim edilecek",
        category=EmailCategory.EDUCATION,
        priority=TaskPriority.MEDIUM,
        deadline=None,
        deadline_confidence=DeadlineConfidence.UNCERTAIN,
        raw_deadline_text="hafta sonuna kadar",
    )

    with pytest.raises(AmbiguousDeadlineError, match="son tarih belirsiz"):
        planner.plan_reminder_for_deadline(vague_proposal)

    # If user provides explicit date, planning succeeds
    explicit_date = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)
    action = planner.plan_reminder_for_deadline(vague_proposal, custom_due_date=explicit_date)
    assert action.due_date == explicit_date
    assert action.target_destination == ActionDestination.APPLE_REMINDERS


def test_deadline_milestone_and_work_block_separation() -> None:
    """Verify that deadline milestone event and preparation work block are separate records."""
    planner = TaskPlanner()
    tz = timezone.utc

    exam_date = datetime(2026, 10, 12, 10, 0, tzinfo=tz)
    exam_end = datetime(2026, 10, 12, 12, 0, tzinfo=tz)

    exam_proposal = TaskProposal(
        source_message_id="msg_exam_1",
        title="YBS Final Sınavı",
        description="Amfi 1'de gerçekleştirilecek",
        category=EmailCategory.EDUCATION,
        priority=TaskPriority.HIGH,
        deadline=exam_date,
        deadline_confidence=DeadlineConfidence.EXACT,
        raw_deadline_text="12 Ekim 10:00",
    )

    # 1. Milestone Event (The Exam itself)
    milestone_action = planner.plan_calendar_milestone_event(
        proposal=exam_proposal,
        start_time=exam_date,
        end_time=exam_end,
    )
    assert milestone_action.action_type == ActionType.CALENDAR_EVENT
    assert milestone_action.start_time == exam_date
    assert milestone_action.end_time == exam_end

    # 2. Reminder Action (Reminder before exam)
    reminder_action = planner.plan_reminder_for_deadline(proposal=exam_proposal)
    assert reminder_action.action_type == ActionType.REMINDER
    assert reminder_action.due_date == exam_date

    # Verify they have distinct action types and identity
    assert milestone_action.action_id != reminder_action.action_id
    assert milestone_action.action_type != reminder_action.action_type


@pytest.mark.asyncio
async def test_plan_work_block_with_mock_bridge() -> None:
    """Verify plan_work_block integrates bridge calendar events and returns candidate slots."""
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    mock_bridge.call.return_value = {
        "events": [
            {
                "id": "ev_existing_1",
                "title": "Meeting",
                "start": "2026-10-07T10:00:00+00:00",
                "end": "2026-10-07T11:00:00+00:00",
            }
        ]
    }

    planner = TaskPlanner(bridge_client=mock_bridge, buffer_minutes=15)
    proposal = TaskProposal(
        source_message_id="msg_prep_1",
        title="Büyük Proje Teslimi",
        description="GitHub reposunu hazırla",
        category=EmailCategory.WORK_CAREER,
        priority=TaskPriority.HIGH,
        deadline=datetime(2026, 10, 10, 23, 59, tzinfo=timezone.utc),
        deadline_confidence=DeadlineConfidence.EXACT,
    )

    action, slots = await planner.plan_work_block(
        proposal=proposal,
        duration_minutes=120,
        target_date_range=(
            datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc),
        ),
    )

    assert action.action_type == ActionType.WORK_BLOCK
    assert action.target_destination == ActionDestination.APPLE_CALENDAR
    assert len(slots) > 0
    assert (action.end_time - action.start_time).total_seconds() == 7200
