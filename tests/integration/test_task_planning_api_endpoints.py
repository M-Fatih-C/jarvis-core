"""Integration tests for Task Planning, Approval, and EventKit Execution REST API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import pytest
from uuid import uuid4

from api.dependencies import (
    set_custom_approval_store,
    set_custom_bridge_client,
    set_custom_email_storage,
)
from api.main import create_app
from core.agent.approval_store import ApprovalStore
from core.email_analysis.schemas import EmailCategory, TaskPriority
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.models import NormalizedEmail
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient


@pytest.fixture
def test_setup():
    storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
    approval_store = ApprovalStore()
    mock_bridge = AsyncMock(spec=MacBridgeClient)

    set_custom_email_storage(storage)
    set_custom_approval_store(approval_store)
    set_custom_bridge_client(mock_bridge)

    app = create_app()
    client = TestClient(app)

    yield client, storage, mock_bridge

    set_custom_email_storage(None)
    set_custom_approval_store(None)
    set_custom_bridge_client(None)
    storage.close()


@pytest.mark.asyncio
async def test_full_api_workflow_proposal_plan_approve_and_trace(test_setup) -> None:
    """Test full API journey:
    1. Seed email & proposal
    2. GET proposals
    3. POST plan-reminder
    4. PATCH customize action
    5. POST request-approval
    6. POST approve
    7. GET source-email
    """
    client, storage, mock_bridge = test_setup
    task_id = uuid4()
    msg_id = "msg_api_test_1"

    # Seed email
    email = NormalizedEmail(
        message_id=msg_id,
        thread_id="t_api_1",
        sender="Prof. Dr. Ahmet <ahmet@univ.edu.tr>",
        sender_email="ahmet@univ.edu.tr",
        recipient="fatih@jarvis.local",
        subject="Proje Raporu Teslimi",
        received_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        body_plain="Lütfen proje raporunuzu 8 Ekim 23:59'a kadar yükleyin.",
        body_preview="Lütfen proje raporunuzu...",
        attachments=[],
        headers={},
        labels=["INBOX"],
        content_hash="hash_api_1",
    )
    await storage.save_email(email, "fatih@jarvis.local")

    # Seed proposal
    await storage.save_task_proposal({
        "task_id": str(task_id),
        "source_message_id": msg_id,
        "title": "Proje Raporunu Teslim Et",
        "description": "Dönem projesi raporu",
        "category": EmailCategory.EDUCATION,
        "priority": TaskPriority.HIGH,
        "deadline": datetime(2026, 10, 8, 23, 59, tzinfo=timezone.utc),
        "deadline_confidence": "exact",
        "proposed_action": "Hatırlatıcı oluştur",
        "status": TaskProposalStatus.PROPOSED,
        "created_at": datetime.now(timezone.utc),
    })

    # Mock bridge responses for create and read-back
    mock_bridge.call.side_effect = [
        {"id": "ek_rem_api_99", "status": "created"},
        {"id": "ek_rem_api_99", "title": "Proje Raporunu Teslim Et (Güncellendi)", "due_at": "2026-10-08T17:00:00+00:00"},
    ]

    # 1. List proposals
    res = client.get("/api/v1/tasks/proposals")
    assert res.status_code == 200
    props = res.json()
    assert len(props) == 1
    assert props[0]["title"] == "Proje Raporunu Teslim Et"

    # 2. Get proposal details
    res = client.get(f"/api/v1/tasks/proposals/{task_id}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["source_email_subject"] == "Proje Raporu Teslimi"

    # 3. Plan reminder
    res = client.post(
        f"/api/v1/tasks/proposals/{task_id}/plan-reminder",
        json={"custom_due_date": "2026-10-08T20:00:00+03:00"},
    )
    assert res.status_code == 200
    action = res.json()
    action_id = action["action_id"]
    assert action["status"] == "PROPOSED"
    assert action["target_destination"] == "apple_reminders"

    # 4. Customize action
    res = client.patch(
        f"/api/v1/tasks/actions/{action_id}/customize",
        json={"title": "Proje Raporunu Teslim Et (Güncellendi)"},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Proje Raporunu Teslim Et (Güncellendi)"

    # 5. Request Approval (PolicyEngine R2 evaluation)
    res = client.post(f"/api/v1/tasks/actions/{action_id}/request-approval")
    assert res.status_code == 200
    app_data = res.json()
    assert app_data["status"] == "waiting_approval"
    assert app_data["action_digest"] is not None

    # 6. Approve and Execute (EventKit mutation + Read-back verification)
    res = client.post(f"/api/v1/tasks/actions/{action_id}/approve")
    assert res.status_code == 200
    exec_data = res.json()
    assert exec_data["status"] == "EXECUTED"
    assert exec_data["external_id"] == "ek_rem_api_99"

    # 7. Trace Origin Email
    res = client.get("/api/v1/tasks/source-email/ek_rem_api_99")
    assert res.status_code == 200
    trace = res.json()
    assert trace["email_subject"] == "Proje Raporu Teslimi"
    assert trace["email_sender_email"] == "ahmet@univ.edu.tr"
    assert trace["task_title"] == "Proje Raporunu Teslim Et"


@pytest.mark.asyncio
async def test_api_list_calendars(test_setup) -> None:
    """Test /api/v1/tasks/calendars endpoint."""
    client, storage, mock_bridge = test_setup
    mock_bridge.call.return_value = {
        "calendars": [
            {
                "id": "cal_icloud_main",
                "title": "Jarvis Work",
                "allows_modifications": True,
                "source_title": "iCloud",
                "source_type": "calDAV",
            }
        ]
    }

    res = client.get("/api/v1/tasks/calendars")
    assert res.status_code == 200
    data = res.json()
    assert len(data["calendars"]) == 1
    assert data["preferred_calendar_id"] == "cal_icloud_main"
