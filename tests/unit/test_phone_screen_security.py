"""Real service code with isolated SQLite and a controlled native bridge (MOCK)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest

from core.agent.approval_store import ApprovalStore
from core.agent.conversation_store import ConversationStore
from core.agent.exceptions import ApprovalIntegrityError
from core.cloud.client_requests import ClientRequests
from core.email_analysis.schemas import TaskProposal, EmailAnalysisResult
from core.memory.crypto import InMemoryKeyProvider, CryptoError
from core.models.messages import ChatMessage, MessageRole
from core.task_planning.approval_service import TaskApprovalService, DuplicateActionError, ReadBackVerificationError
from core.task_planning.planner import TaskPlanner
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.models import NormalizedEmail
from integrations.gmail.storage import EmailStorage


async def fixture():
    storage = EmailStorage(encryption_enabled=False)
    bridge = SimpleNamespace(call=AsyncMock())
    approval = TaskApprovalService(storage, approval_store=ApprovalStore(), bridge_client=bridge)
    screens = ClientRequests(owner="owner", storage=storage, planner=TaskPlanner(bridge_client=bridge),
        calendar_manager=None, approval_service=approval)
    task = TaskProposal(source_message_id="synthetic", title="Synthetic test", description="Test only",
        category="general", priority="LOW", deadline=datetime(2026, 10, 4, 15, 30, tzinfo=timezone.utc), deadline_confidence="exact")
    await storage.save_task_proposal(task.model_dump())
    return screens, storage, bridge, task


@pytest.mark.asyncio
async def test_phone_planning_requires_distinct_digest_bound_approval():
    screens, storage, bridge, task = await fixture()
    action = await screens.handle("client.plan_reminder", {"task_id": str(task.task_id)}, "owner")
    duplicate = await screens.handle("client.plan_reminder", {"task_id": str(task.task_id)}, "owner")
    assert duplicate["action_id"] == action["action_id"]
    bridge.call.assert_not_awaited()
    waiting = await screens.handle("client.request_approval", {"action_id": action["action_id"]}, "owner")
    payload = {"task_action_id": waiting["action_id"], "approval_id": waiting["approval_id"], "decision": "approved", "action_digest": "tampered"}
    with pytest.raises(ApprovalIntegrityError):
        await screens.approve(payload, "owner")
    bridge.call.assert_not_awaited()
    payload["action_digest"] = waiting["action_digest"]
    bridge.call.side_effect = [{"id": "synthetic-record"}, {"id": "synthetic-record", "title": task.title, "due_at": task.deadline.isoformat()}]
    executed = await screens.approve(payload, "owner")
    assert executed["status"] == "EXECUTED"
    assert bridge.call.await_count == 2
    with pytest.raises(DuplicateActionError):
        await screens.approve(payload, "owner")
    assert bridge.call.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["client.emails", "client.proposals", "client.plan_reminder", "client.request_approval"])
async def test_phone_screen_requests_reject_another_user(name):
    screens, _, bridge, _ = await fixture()
    with pytest.raises(PermissionError):
        await screens.handle(name, {}, "intruder")
    bridge.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_phone_gateway_does_not_allow_arbitrary_tool_execution_or_observe_writes():
    screens, _, bridge, task = await fixture()
    with pytest.raises(ValueError):
        await screens.handle("client.execute", {"tool": "calendar.create_event", "is_approved": True}, "owner")
    from core.models.agent import AgentMode
    screens.mode = AgentMode.OBSERVE
    with pytest.raises(PermissionError):
        await screens.handle("client.plan_reminder", {"task_id": str(task.task_id)}, "owner")
    bridge.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [{}, {"id": "wrong", "title": "Synthetic test"},
    {"id": "created", "title": "Synthetic test", "due_at": "2026-10-04T16:30:00+00:00"}, RuntimeError("bridge offline")])
async def test_unverified_write_is_not_success_and_cannot_be_replayed(readback):
    screens, storage, bridge, task = await fixture()
    action = await screens.handle("client.plan_reminder", {"task_id": str(task.task_id)}, "owner")
    waiting = await screens.handle("client.request_approval", {"action_id": action["action_id"]}, "owner")
    bridge.call.side_effect = [{"id": "created"}, readback]
    payload = {"task_action_id": waiting["action_id"], "approval_id": waiting["approval_id"], "decision": "approved", "action_digest": waiting["action_digest"]}
    with pytest.raises(ReadBackVerificationError):
        await screens.approve(payload, "owner")
    stored = await storage.get_task_action(action["action_id"])
    assert stored.status == TaskProposalStatus.FAILED
    assert stored.external_id == "created"
    with pytest.raises(DuplicateActionError):
        await screens.approve(payload, "owner")
    assert bridge.call.await_count == 2


@pytest.mark.asyncio
async def test_dismissed_approval_cannot_execute_later():
    screens, _, bridge, task = await fixture()
    action = await screens.handle("client.plan_reminder", {"task_id": str(task.task_id)}, "owner")
    waiting = await screens.handle("client.request_approval", {"action_id": action["action_id"]}, "owner")
    await screens.handle("client.dismiss", {"action_id": action["action_id"]}, "owner")
    with pytest.raises(Exception):
        await screens.approve({"task_action_id": action["action_id"], "approval_id": waiting["approval_id"], "decision": "approved", "action_digest": waiting["action_digest"]}, "owner")
    bridge.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_summary_is_persisted_encrypted_without_rescanning_or_notifying(tmp_path):
    key = InMemoryKeyProvider()
    path = str(tmp_path / "mail.db")
    storage = EmailStorage(path, key_provider=key)
    email = NormalizedEmail(message_id="synthetic", thread_id="t", sender="Test", sender_email="test@example.invalid",
        recipient="owner@example.invalid", subject="Synthetic", received_at=datetime.now(timezone.utc), content_hash="test")
    await storage.save_email(email, "owner")
    analysis = EmailAnalysisResult(category="general", importance="LOW", summary="PRIVATE SUMMARY TEST", proposed_action="Review")
    await storage.save_analysis(email.message_id, analysis)
    assert "PRIVATE SUMMARY TEST" not in storage._conn.execute("SELECT summary FROM email_analyses").fetchone()[0]
    storage.close()
    restarted = EmailStorage(path, key_provider=key)
    rows = await restarted.list_email_summaries()
    assert rows[0]["body_preview"] == "PRIVATE SUMMARY TEST"
    assert await restarted.is_notification_sent(email.message_id) is False
    restarted.close()


@pytest.mark.asyncio
async def test_conversation_restart_encryption_and_owner_isolation(tmp_path):
    path = tmp_path / "history.db"
    keys = InMemoryKeyProvider()
    store = ConversationStore(path, keys)
    messages = [ChatMessage(role=MessageRole.USER, content="PRIVATE CONTEXT TEST")]
    await store.save("owner", "conversation", messages)
    encrypted = store.connection.execute("SELECT encrypted FROM conversations").fetchone()[0]
    assert "PRIVATE CONTEXT TEST" not in encrypted
    restored = ConversationStore(path, keys)
    assert await restored.load("owner", "conversation") == messages
    assert await restored.load("intruder", "conversation") == []
    with restored.connection:
        restored.connection.execute("INSERT INTO conversations VALUES (?, ?, ?)", ("intruder", "conversation", encrypted))
    with pytest.raises(CryptoError):
        await restored.load("intruder", "conversation")

@pytest.mark.asyncio
async def test_local_memory_routes_cannot_bypass_private_category_consent():
    from fastapi import HTTPException
    from api.routes.memory import list_memories, get_memory, update_memory, delete_memory, UpdateMemoryRequest
    from core.memory.models import MemoryRecord
    record = MemoryRecord(kind="profile", sensitivity="private", encrypted_content='{"synthetic": true}')
    repo = SimpleNamespace(list=AsyncMock(return_value=[record]), get=AsyncMock(return_value=record),
        update=AsyncMock(), delete=AsyncMock())
    memory = SimpleNamespace(repository=repo, _key_provider=SimpleNamespace(get_or_create_memory_key=AsyncMock()))
    assert await list_memories(kind=None,sensitivity=None,status_filter=None,limit=20,memory=memory) == []
    for request in [get_memory(record.id, memory), update_memory(record.id, UpdateMemoryRequest(content="tamper"), memory), delete_memory(record.id, memory)]:
        with pytest.raises(HTTPException) as raised:
            await request
        assert raised.value.status_code == 403
    memory._key_provider.get_or_create_memory_key.assert_not_awaited()
    repo.update.assert_not_awaited(); repo.delete.assert_not_awaited()

@pytest.mark.asyncio
async def test_uncertain_write_stays_blocked_even_after_approval_store_restart():
    screens, storage, bridge, task = await fixture()
    action = await screens.handle("client.plan_reminder", {"task_id": str(task.task_id)}, "owner")
    waiting = await screens.handle("client.request_approval", {"action_id": action["action_id"]}, "owner")
    bridge.call.side_effect = TimeoutError("reply lost after possible save")
    with pytest.raises(Exception):
        await screens.approve({"task_action_id": action["action_id"], "approval_id": waiting["approval_id"], "decision": "approved", "action_digest": waiting["action_digest"]}, "owner")
    assert (await storage.get_task_action(action["action_id"])).status == TaskProposalStatus.EXECUTING
    screens.approvals = TaskApprovalService(storage, approval_store=ApprovalStore(), bridge_client=bridge)
    with pytest.raises(ValueError):
        await screens.handle("client.request_approval", {"action_id": action["action_id"]}, "owner")
    assert bridge.call.await_count == 1
