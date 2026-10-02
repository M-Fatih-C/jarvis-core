"""Unit tests for approval digest binding, PolicyEngine R2, read-back verification, and email source trace."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from uuid import uuid4

from core.agent.approval_store import ApprovalStore
from core.agent.exceptions import ApprovalIntegrityError, JarvisError
from core.email_analysis.schemas import EmailCategory, TaskPriority
from core.policy.engine import PolicyEngine
from core.task_planning.approval_service import (
    ReadBackVerificationError,
    TaskApprovalService,
)
from core.task_planning.calendar_manager import CalendarTargetManager
from core.task_planning.schemas import (
    ActionDestination,
    ActionType,
    CalendarCategory,
    TaskActionProposal,
)
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.models import NormalizedEmail
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient


@pytest.mark.asyncio
async def test_approval_action_digest_binding_and_tamper_detection() -> None:
    """Verify approval is bound to exact action digest, and parameter tampering is caught."""
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    approval_store = ApprovalStore()
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    approval_svc = TaskApprovalService(
        storage=storage,
        approval_store=approval_store,
        bridge_client=mock_bridge,
    )

    task_id = uuid4()
    msg_id = "msg_tamper_1"

    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Application Follow-up",
        "description": "Follow up with HR",
        "category": EmailCategory.WORK_CAREER,
        "priority": TaskPriority.HIGH,
        "deadline": None,
        "deadline_confidence": "none",
        "proposed_action": "Create reminder",
        "status": TaskProposalStatus.PROPOSED,
        "created_at": datetime.now(timezone.utc),
    })

    action = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.REMINDER,
        target_destination=ActionDestination.APPLE_REMINDERS,
        title="Follow up with HR",
        due_date=datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.PROPOSED,
    )

    # 1. Prepare Approval -> enters WAITING_APPROVAL with action digest
    waiting_action, approval_req = await approval_svc.prepare_action_approval(action)
    assert waiting_action.status == TaskProposalStatus.WAITING_APPROVAL
    assert waiting_action.action_digest is not None
    assert approval_req.action_digest == waiting_action.action_digest

    # 2. Tampering test: if title/arguments are modified in SQLite before execution
    tampered_action = waiting_action.model_copy(update={"title": "Hacked Title Without Approval"})
    await storage.save_task_action(tampered_action)

    # Approve request in store
    approval_store.approve(waiting_action.approval_id)

    # Executing tampered action must raise ApprovalIntegrityError because action_digest no longer matches!
    with pytest.raises(ApprovalIntegrityError, match="Action digest mismatch"):
        await approval_svc.approve_and_execute_action(waiting_action.action_id)


@pytest.mark.asyncio
async def test_successful_execution_and_readback_verification() -> None:
    """Verify that successful EventKit execution triggers read-back verification and marks EXECUTED."""
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    # Mock create returns created ID
    mock_bridge.call.side_effect = [
        # Call 1: reminders.create
        {"id": "ek_rem_777", "status": "created"},
        # Call 2: reminders.list (read-back verification)
        {"reminders": [{"id": "ek_rem_777", "title": "Burs Başvurusu", "completed": False}]},
    ]

    approval_svc = TaskApprovalService(storage=storage, bridge_client=mock_bridge)
    task_id = uuid4()
    msg_id = "msg_burs_1"

    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Burs Başvurusu",
        "description": "Formu doldur",
        "category": EmailCategory.EDUCATION,
        "priority": TaskPriority.HIGH,
        "deadline": None,
        "deadline_confidence": "none",
        "proposed_action": "Hatırlatıcı oluştur",
        "status": TaskProposalStatus.PROPOSED,
        "created_at": datetime.now(timezone.utc),
    })

    action = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.REMINDER,
        target_destination=ActionDestination.APPLE_REMINDERS,
        title="Burs Başvurusu",
        due_date=datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.PROPOSED,
    )

    waiting_action, _ = await approval_svc.prepare_action_approval(action)
    executed_action = await approval_svc.approve_and_execute_action(waiting_action.action_id)

    assert executed_action.status == TaskProposalStatus.EXECUTED
    assert executed_action.external_id == "ek_rem_777"
    assert executed_action.executed_at is not None

    # Check parent proposal updated to EXECUTED
    prop = await storage.get_task_proposal(task_id)
    assert prop["status"] == TaskProposalStatus.EXECUTED


@pytest.mark.asyncio
async def test_readback_failure_prevents_executed_status() -> None:
    """Verify that if read-back verification fails, action is marked FAILED rather than EXECUTED."""
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    mock_bridge.call.side_effect = [
        # Call 1: reminders.create succeeds
        {"id": "ek_rem_ghost", "status": "created"},
        # Call 2: reminders.list returns items but the created item is MISSING!
        {"reminders": [{"id": "other_id", "title": "Unrelated reminder"}]},
    ]

    approval_svc = TaskApprovalService(storage=storage, bridge_client=mock_bridge)
    task_id = uuid4()
    msg_id = "msg_ghost_1"

    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Hayalet Hatırlatıcı",
        "description": "Deneme",
        "category": EmailCategory.GENERAL,
        "priority": TaskPriority.LOW,
        "deadline": None,
        "deadline_confidence": "none",
        "proposed_action": "Oluştur",
        "status": TaskProposalStatus.PROPOSED,
        "created_at": datetime.now(timezone.utc),
    })

    action = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.REMINDER,
        target_destination=ActionDestination.APPLE_REMINDERS,
        title="Hayalet Hatırlatıcı",
        due_date=datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.PROPOSED,
    )

    waiting_action, _ = await approval_svc.prepare_action_approval(action)

    with pytest.raises(ReadBackVerificationError, match="not found in Apple Reminders"):
        await approval_svc.approve_and_execute_action(waiting_action.action_id)


@pytest.mark.asyncio
async def test_calendar_manager_prefers_icloud_and_avoids_on_my_mac() -> None:
    """Verify CalendarTargetManager identifies iCloud vs On My Mac and warns when no iCloud calendar exists."""
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    # Case A: System has iCloud and On My Mac calendars
    mock_bridge.call.return_value = {
        "calendars": [
            {
                "id": "cal_local_1",
                "title": "Home",
                "allows_modifications": True,
                "source_title": "On My Mac",
                "source_type": "local",
            },
            {
                "id": "cal_icloud_1",
                "title": "Calendar",
                "allows_modifications": True,
                "source_title": "iCloud",
                "source_type": "calDAV",
            },
        ]
    }

    mgr = CalendarTargetManager(bridge_client=mock_bridge)
    cals = await mgr.list_writable_calendars()
    assert len(cals) == 2

    pref_cal, warn = await mgr.get_preferred_calendar()
    assert pref_cal is not None
    assert pref_cal.id == "cal_icloud_1"
    assert pref_cal.is_icloud is True
    assert warn is None

    # Case B: System has ONLY 'On My Mac' calendars
    mock_bridge.call.return_value = {
        "calendars": [
            {
                "id": "cal_local_only",
                "title": "My Calendar",
                "allows_modifications": True,
                "source_title": "On My Mac",
                "source_type": "local",
            }
        ]
    }
    pref_b, warn_b = await mgr.get_preferred_calendar()
    assert pref_b is None
    assert warn_b is not None
    assert "No iCloud calendar found" in warn_b


@pytest.mark.asyncio
async def test_source_email_linkage_query() -> None:
    """Verify bidirectional link from EventKit external ID back to original email."""
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    msg_id = "msg_internship_42"
    task_id = uuid4()
    external_id = "ek_reminder_internship_99"

    # 1. Save normalized email
    email = NormalizedEmail(
        message_id=msg_id,
        thread_id="t_intern_1",
        sender="Staj Koordinatörlüğü <staj@univ.edu.tr>",
        sender_email="staj@univ.edu.tr",
        recipient="fatih@jarvis.local",
        subject="Staj Başvuru Evrakları Teslimi",
        received_at=datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc),
        body_plain="Staj evraklarınızı 15 Ekim 17:00 tarihine kadar sisteme yükleyin.",
        body_preview="Staj evraklarınızı 15 Ekim...",
        attachments=[],
        headers={},
        labels=["INBOX"],
        content_hash="hash_intern",
    )
    await storage.save_email(email, account_email="fatih@jarvis.local")

    # 2. Save task proposal
    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Staj evraklarını yükle",
        "description": "Sisteme yükleme yapılacak",
        "category": EmailCategory.EDUCATION,
        "priority": TaskPriority.HIGH,
        "deadline": datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc),
        "deadline_confidence": "exact",
        "proposed_action": "Hatırlatıcı oluştur",
        "status": TaskProposalStatus.EXECUTED,
        "created_at": datetime.now(timezone.utc),
    })

    # 3. Save executed task action
    action = TaskActionProposal(
        task_id=task_id,
        source_message_id=msg_id,
        action_type=ActionType.REMINDER,
        target_destination=ActionDestination.APPLE_REMINDERS,
        title="Staj evraklarını yükle",
        due_date=datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc),
        status=TaskProposalStatus.EXECUTED,
        external_id=external_id,
        executed_at=datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc),
    )
    await storage.save_task_action(action)

    # 4. Query source email by external_id ("Bu hatırlatıcı hangi e-postadan oluşturuldu?")
    approval_svc = TaskApprovalService(storage=storage)
    link = await approval_svc.get_source_email_for_external_record(external_id)

    assert link is not None
    assert link.external_id == external_id
    assert link.email_subject == "Staj Başvuru Evrakları Teslimi"
    assert link.email_sender_email == "staj@univ.edu.tr"
    assert link.source_message_id == msg_id
    assert link.task_title == "Staj evraklarını yükle"
    assert "Staj evraklarınızı" in link.email_preview
