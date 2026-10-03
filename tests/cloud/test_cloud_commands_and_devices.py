"""Tests for cloud command queue, leasing, idempotency, and device heartbeat."""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.cloud.command_worker import CommandWorker
from core.cloud.models import CloudCommand, CommandStatus, DeviceRecord, DeviceStatus
from core.cloud.repositories import InMemoryCommandRepository, InMemoryDeviceRepository
from core.cloud.device_service import DeviceService
from core.config.settings import Settings
from core.models.messages import ChatMessage, MessageRole
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry
from core.llm.mock_adapter import MockLLMAdapter


@pytest.fixture
def test_runtime() -> AgentRuntime:
    settings = Settings()
    reg = ToolRegistry()
    register_mock_tools(reg)
    pol = PolicyEngine()
    exec_ = ToolExecutor(reg, pol)
    llm = MockLLMAdapter()
    return AgentRuntime(
        llm_adapter=llm,
        tool_registry=reg,
        policy_engine=pol,
        tool_executor=exec_,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_device_service_registration_and_heartbeat() -> None:
    repo = InMemoryDeviceRepository()
    svc = DeviceService(repository=repo)

    dev = await svc.register_self()
    assert dev.status == DeviceStatus.ONLINE
    assert dev.device_id == "mac-mini-main"

    beat_ok = await svc.beat(status=DeviceStatus.ONLINE)
    assert beat_ok is True

    stored = await repo.get("mac-mini-main")
    assert stored is not None
    assert stored.status == DeviceStatus.ONLINE


@pytest.mark.asyncio
async def test_command_leasing_and_execution(test_runtime: AgentRuntime) -> None:
    cmd_repo = InMemoryCommandRepository()
    worker = CommandWorker(command_repo=cmd_repo, runtime=test_runtime)

    cmd = CloudCommand(
        id="cmd_status_1",
        type="tool_execution",
        name="system.get_status",
        source_device="ios-client",
        idempotency_key="idemp_1001",
    )
    await cmd_repo.create(cmd)

    # Poll once
    executed_cmd = await worker.poll_once()
    assert executed_cmd is not None
    assert executed_cmd.id == "cmd_status_1"
    assert executed_cmd.status == CommandStatus.COMPLETED
    assert executed_cmd.result is not None
    assert executed_cmd.result["data"]["platform"] == "macOS"


@pytest.mark.asyncio
async def test_command_idempotency_prevents_duplicate_creation() -> None:
    cmd_repo = InMemoryCommandRepository()
    cmd1 = CloudCommand(
        id="cmd_id_1",
        name="system.get_status",
        source_device="ios",
        idempotency_key="unique_key_abc",
    )
    cmd2 = CloudCommand(
        id="cmd_id_2",  # Different ID, same idempotency key
        name="system.get_status",
        source_device="ios",
        idempotency_key="unique_key_abc",
    )

    created1 = await cmd_repo.create(cmd1)
    created2 = await cmd_repo.create(cmd2)

    assert created1.id == created2.id == "cmd_id_1"
    cmds = await cmd_repo.list()
    assert len(cmds) == 1


@pytest.mark.asyncio
async def test_expired_lease_reclaimed_by_worker() -> None:
    cmd_repo = InMemoryCommandRepository()

    now = datetime.now(timezone.utc)
    expired_cmd = CloudCommand(
        id="cmd_crashed_worker",
        name="system.get_status",
        source_device="ios",
        idempotency_key="crash_key_1",
        status=CommandStatus.LEASED,
        lease_owner="crashed_mac_01",
        lease_expires_at=now - timedelta(seconds=10),  # Lease expired 10s ago
        attempts=1,
    )
    await cmd_repo.create(expired_cmd)

    # Worker leases next available command
    leased = await cmd_repo.lease_next_command(worker_id="active_mac_02", lease_duration_seconds=30)
    assert leased is not None
    assert leased.id == "cmd_crashed_worker"
    assert leased.lease_owner == "active_mac_02"
    assert leased.attempts == 2

@pytest.mark.parametrize('decision', ['approved', 'rejected'])
async def test_mobile_approval_uses_real_runtime_once(test_runtime, decision):
    """Real worker/runtime/store integration; model and Apple tool are mocks."""
    settings = Settings(firebase_enabled=True, jarvis_uid='synthetic-owner')
    repo = InMemoryCommandRepository()
    worker = CommandWorker(repo, test_runtime, settings=settings)
    await repo.create(CloudCommand(id='ask', user_id='synthetic-owner', name='chat', type='agent_run',
        source_device='synthetic-phone', idempotency_key='ask',
        payload={'input': "Yarın saat 19'da test çalışmayı hatırlat.", 'conversation_id': 'shared'}))
    proposed = await worker.poll_once()
    assert proposed.status == CommandStatus.WAITING_APPROVAL
    approval_id = proposed.result['approval_id']
    payload = {'approval_id': approval_id, 'decision': decision, 'action_digest': proposed.result['action_digest']}
    await repo.create(CloudCommand(id='decision', user_id='synthetic-owner', name='approval', type='approval_response',
        source_device='synthetic-phone', idempotency_key='decision', payload=payload))
    result = await worker.poll_once()
    assert result.status == CommandStatus.COMPLETED
    from uuid import UUID
    req = test_runtime.approval_store.get(UUID(approval_id))
    run = test_runtime.get_run(req.agent_run_id)
    assert run.tool_call_count == (1 if decision == 'approved' else 0)
    assert len(test_runtime._conversations[('synthetic-owner', 'shared')]) == 2
    await repo.create(CloudCommand(id='replay', user_id='synthetic-owner', name='approval', type='approval_response',
        source_device='synthetic-phone', idempotency_key='replay', payload=payload))
    assert (await worker.poll_once()).status == CommandStatus.FAILED


async def test_production_identity_cannot_be_claimed_in_payload(test_runtime):
    repo = InMemoryCommandRepository()
    worker = CommandWorker(repo, test_runtime, settings=Settings(firebase_enabled=True, jarvis_uid='owner'))
    await repo.create(CloudCommand(id='forged', name='chat', type='agent_run', source_device='phone',
        idempotency_key='forged', payload={'user_id':'owner', 'input':'hello'}))
    assert (await worker.poll_once()).status == CommandStatus.FAILED

async def test_verbal_approval_request_repairs_to_secure_card(test_runtime):
    from core.llm.schemas import LLMResponse
    from core.models.tools import ToolCall
    from core.agent.state_machine import AgentState
    test_runtime._llm.queue_response(LLMResponse(content='Onayınızı bekliyorum.'))
    test_runtime._llm.queue_response(LLMResponse(tool_calls=[ToolCall(id='proposal', name='reminders.create', arguments={'title':'Synthetic test'})]))
    run = await test_runtime.run('Create a synthetic test reminder', user_id='synthetic-owner')
    assert run.state == AgentState.WAITING_APPROVAL
    assert run.tool_call_count == 0
    assert test_runtime.approval_store.get(run.pending_approval_id).user_id == 'synthetic-owner'


async def test_partial_approved_action_is_reported_when_later_step_hits_limit(test_runtime):
    from core.llm.schemas import LLMResponse
    from core.models.tools import ToolCall
    from core.agent.state_machine import AgentState
    test_runtime._settings.max_tool_calls = 1
    for n in range(2):
        test_runtime._llm.queue_response(LLMResponse(tool_calls=[ToolCall(
            id=f'proposal-{n}', name='reminders.create', arguments={'title': f'Synthetic task {n}', 'due_at': '2026-10-04T18:00:00+03:00'}
        )]))
    proposed = await test_runtime.run('Create two synthetic reminders', user_id='synthetic-owner')
    completed = await test_runtime.resume_approval(proposed.pending_approval_id, user_id='synthetic-owner')
    assert completed.state == AgentState.FAILED
    assert completed.completed_action_count == 1
    assert completed.tool_call_count == 1
    assert '1 işlem tamamlandı ve doğrulandı' in completed.error
