"""Unit tests for task planning 7 lifecycle states, deterministic transitions, and idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from uuid import uuid4

from core.email_analysis.schemas import EmailCategory, TaskPriority
from core.task_planning.approval_service import DuplicateActionError, TaskApprovalService
from core.task_planning.schemas import (
    ActionDestination,
    ActionType,
    CalendarCategory,
    TaskActionProposal,
)
from core.task_planning.state import (
    InvalidStateTransitionError,
    TaskProposalStatus,
    transition_task_status,
)
from integrations.gmail.storage import EmailStorage


def test_lifecycle_allowed_deterministic_transitions() -> None:
    """Verify that all allowed deterministic transitions succeed."""
    # PROPOSED -> WAITING_APPROVAL -> APPROVED -> EXECUTING -> EXECUTED
    s = TaskProposalStatus.PROPOSED
    s = transition_task_status(s, TaskProposalStatus.WAITING_APPROVAL)
    assert s == TaskProposalStatus.WAITING_APPROVAL

    s = transition_task_status(s, TaskProposalStatus.APPROVED)
    assert s == TaskProposalStatus.APPROVED

    s = transition_task_status(s, TaskProposalStatus.EXECUTING)
    assert s == TaskProposalStatus.EXECUTING

    s = transition_task_status(s, TaskProposalStatus.EXECUTED)
    assert s == TaskProposalStatus.EXECUTED

    # Reopening from DISMISSED -> PROPOSED
    s_d = TaskProposalStatus.DISMISSED
    s_p = transition_task_status(s_d, TaskProposalStatus.PROPOSED)
    assert s_p == TaskProposalStatus.PROPOSED

    # Failure recovery: EXECUTING -> FAILED -> WAITING_APPROVAL
    s_fail = transition_task_status(TaskProposalStatus.EXECUTING, TaskProposalStatus.FAILED)
    assert s_fail == TaskProposalStatus.FAILED
    s_retry = transition_task_status(s_fail, TaskProposalStatus.WAITING_APPROVAL)
    assert s_retry == TaskProposalStatus.WAITING_APPROVAL


def test_lifecycle_disallowed_transitions_raise() -> None:
    """Verify that illegal transitions raise InvalidStateTransitionError."""
    # Cannot jump directly from PROPOSED to EXECUTED without approval and execution
    with pytest.raises(InvalidStateTransitionError):
        transition_task_status(TaskProposalStatus.PROPOSED, TaskProposalStatus.EXECUTED)

    # Terminal state: cannot mutate once EXECUTED
    with pytest.raises(InvalidStateTransitionError):
        transition_task_status(TaskProposalStatus.EXECUTED, TaskProposalStatus.PROPOSED)

    with pytest.raises(InvalidStateTransitionError):
        transition_task_status(TaskProposalStatus.EXECUTED, TaskProposalStatus.WAITING_APPROVAL)


def test_lifecycle_status_case_insensitive_equality() -> None:
    """Verify TaskProposalStatus behaves case-insensitively with strings."""
    status = TaskProposalStatus.PROPOSED
    assert status == "PROPOSED"
    assert status == "proposed"
    assert "proposed" == status
    assert status == TaskProposalStatus.PROPOSED
    assert TaskProposalStatus.from_str("completed") == TaskProposalStatus.EXECUTED


@pytest.mark.asyncio
async def test_sqlite_storage_idempotency_constraint() -> None:
    """Verify SQLite unique index on (source_message_id, task_id, action_type)."""
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    task_id = uuid4()
    msg_id = "msg_idemp_100"

    # Save parent task proposal
    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Idempotency Test Task",
        "description": "Test task",
        "category": EmailCategory.WORK_CAREER,
        "priority": TaskPriority.HIGH,
        "deadline": None,
        "deadline_confidence": "none",
        "proposed_action": "Review",
        "status": TaskProposalStatus.PROPOSED,
        "created_at": datetime.now(timezone.utc),
    })

    action1 = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.REMINDER,
        target_destination=ActionDestination.APPLE_REMINDERS,
        title="Follow up application",
        due_date=datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.PROPOSED,
    )
    await storage.save_task_action(action1)

    # Fetching by idempotency key
    found = await storage.get_task_action_by_idempotency_key(msg_id, task_id, ActionType.REMINDER.value)
    assert found is not None
    assert found.action_id == action1.action_id
    assert found.title == "Follow up application"

    # Saving another action with same (source_message_id, task_id, action_type) updates rather than duplicates
    action2 = TaskActionProposal(
        action_id=action1.action_id,
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.REMINDER,
        target_destination=ActionDestination.APPLE_REMINDERS,
        title="Follow up application (Updated)",
        due_date=datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.PROPOSED,
    )
    await storage.save_task_action(action2)

    actions = await storage.get_task_actions_for_task(task_id)
    assert len(actions) == 1
    assert actions[0].title == "Follow up application (Updated)"


@pytest.mark.asyncio
async def test_approval_service_rejects_duplicate_executed_action() -> None:
    """Verify TaskApprovalService enforces idempotency and rejects duplicate proposal."""
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    task_id = uuid4()
    msg_id = "msg_idemp_200"

    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Exam Prep",
        "description": "Exam task",
        "category": EmailCategory.EDUCATION,
        "priority": TaskPriority.HIGH,
        "deadline": None,
        "deadline_confidence": "none",
        "proposed_action": "Study",
        "status": TaskProposalStatus.EXECUTED,
        "created_at": datetime.now(timezone.utc),
    })

    executed_action = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.WORK_BLOCK,
        target_destination=ActionDestination.APPLE_CALENDAR,
        title="Çalışma Bloğu: Exam Prep",
        start_time=datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.EXECUTED,
        external_id="ek_calendar_ev_999",
    )
    await storage.save_task_action(executed_action)

    approval_svc = TaskApprovalService(storage=storage)

    # Attempting to submit another action for the same task and action_type must raise DuplicateActionError
    new_duplicate_action = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.WORK_BLOCK,
        target_destination=ActionDestination.APPLE_CALENDAR,
        title="Duplicate Prep Block",
        start_time=datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.PROPOSED,
    )

    with pytest.raises(DuplicateActionError, match="Mükerrer işlem engellendi"):
        await approval_svc.prepare_action_approval(new_duplicate_action)
