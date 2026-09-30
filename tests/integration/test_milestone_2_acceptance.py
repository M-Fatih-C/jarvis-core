"""Comprehensive Milestone 2 acceptance test suite.

Verifies:
- Scenarios M1 to M5 (Memory extraction, cross-session recall, contradiction, private encryption, secret protection)
- Scenarios C1 to C3 (Cloud command execution, idempotency, offline recovery)
- Full 'Jarvis beni öğreniyor' cross-session acceptance flow
"""

import asyncio
from datetime import datetime, timezone
import json
import pytest
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.cloud.command_worker import CommandWorker
from core.cloud.models import CloudCommand, CommandStatus
from core.cloud.repositories import InMemoryCommandRepository
from core.config.settings import Settings
from core.memory.crypto import InMemoryKeyProvider, MemoryEncryptor
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryCandidate,
    MemoryFilters,
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from core.memory.service import MemoryService
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry
from core.llm.mock_adapter import MockLLMAdapter
from core.llm.schemas import LLMResponse


@pytest.fixture
def memory_environment() -> tuple[MemoryService, AgentRuntime, MockLLMAdapter]:
    settings = Settings()
    repo = SQLiteMemoryRepository(":memory:")
    embedder = DeterministicMockEmbeddingProvider(dimensions=384)
    key_prov = InMemoryKeyProvider()
    mem_service = MemoryService(
        repository=repo,
        embedding_provider=embedder,
        key_provider=key_prov,
        settings=settings,
    )

    reg = ToolRegistry()
    register_mock_tools(reg)
    pol = PolicyEngine()
    exec_ = ToolExecutor(reg, pol)
    llm = MockLLMAdapter()

    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=reg,
        policy_engine=pol,
        tool_executor=exec_,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        memory_service=mem_service,
        settings=settings,
    )
    return mem_service, runtime, llm


# ============================================================================
# SCENARIO M1 & M2: Learn Preference and Recall in Completely New Session
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_m1_and_m2_learn_and_recall_preference(
    memory_environment: tuple[MemoryService, AgentRuntime, MockLLMAdapter],
) -> None:
    mem_service, runtime, llm = memory_environment

    # Session 1: User teaches preference
    session1_prompt = "Hafta içi yan projelerimle 18:00'den sonra ilgilenmek istiyorum."
    llm.queue_response(LLMResponse(
        content="Anladım, hafta içi yan projelerinizle 18:00'den sonra ilgilenme tercihinizi kaydettim."
    ))

    run1 = await runtime.run(session1_prompt)
    assert run1.state == AgentState.COMPLETED

    # Verify memory was extracted and stored
    active_mems = await mem_service.repository.list(filters=MemoryFilters(status=MemoryStatus.ACTIVE))
    assert len(active_mems) >= 1
    pref_mem = next((m for m in active_mems if m.kind == MemoryKind.PREFERENCE), None)
    assert pref_mem is not None
    assert "18:00" in (pref_mem.content or "")
    assert pref_mem.status == MemoryStatus.ACTIVE

    # Session 2: Completely NEW AgentRun / Separate conversation session
    # Destroy session 1 state and create completely fresh runtime sharing only the persisted memory store
    fresh_runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=runtime._tools,
        policy_engine=runtime._policy,
        tool_executor=runtime._executor,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        memory_service=mem_service,
    )

    session2_prompt = "Yarın Jarvis projesine iki saat ayırmak istiyorum."
    # Mock LLM simulates reading the injected context and recommending after 18:00
    llm.queue_response(LLMResponse(
        content="Tercihiniz doğrultusunda, yarın akşam 19:00 - 21:00 arasını Jarvis projesine ayırmayı öneriyorum."
    ))

    run2 = await fresh_runtime.run(session2_prompt)
    assert run2.state == AgentState.COMPLETED
    assert "19:00" in run2.final_response

    # Verify that the memory context was indeed injected into session 2's system message
    session2_context = fresh_runtime._contexts[run2.id]
    sys_msg = session2_context[0]
    assert sys_msg.role == MessageRole.SYSTEM
    assert "<memory_context>" in sys_msg.content
    assert "User prefers working on side projects after 18:00 on weekdays." in sys_msg.content


# ============================================================================
# SCENARIO M3: Preference Update & Contradiction Resolution
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_m3_preference_update_supersedes_old(
    memory_environment: tuple[MemoryService, AgentRuntime, MockLLMAdapter],
) -> None:
    mem_service, runtime, llm = memory_environment

    # 1. Old preference: after 18:00
    old_cand = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        content="User prefers working on side projects after 18:00 on weekdays.",
        subject="user",
        predicate="side_project_work_time",
        value="after_18_weekdays",
        confidence=0.98,
        source_type=MemorySourceType.EXPLICIT_USER,
    )
    old_rec = await mem_service.store_candidate(old_cand)
    assert old_rec is not None
    assert old_rec.status == MemoryStatus.ACTIVE

    # 2. Later user updates preference: after 20:00
    new_cand = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        content="User prefers working on side projects after 20:00 on weekdays.",
        subject="user",
        predicate="side_project_work_time",
        value="after_20_weekdays",
        confidence=0.99,
        source_type=MemorySourceType.EXPLICIT_USER,
    )
    new_rec = await mem_service.store_candidate(new_cand)
    assert new_rec is not None
    assert new_rec.status == MemoryStatus.ACTIVE
    assert new_rec.supersedes == old_rec.id

    # Verify old record is SUPERSEDED
    old_fetched = await mem_service.repository.get(old_rec.id)
    assert old_fetched is not None
    assert old_fetched.status == MemoryStatus.SUPERSEDED

    # Retrieval only returns active memory
    hits = await mem_service.retriever.retrieve("side project time")
    active_hits = [h for h in hits if h.record.status == MemoryStatus.ACTIVE]
    assert len(active_hits) == 1
    assert "20:00" in (active_hits[0].record.content or "")


# ============================================================================
# SCENARIO M4: Private Memory Client-Side Encryption
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_m4_private_memory_client_side_encryption(
    memory_environment: tuple[MemoryService, AgentRuntime, MockLLMAdapter],
) -> None:
    mem_service, runtime, _ = memory_environment

    private_text = "Hassas sağlık notu: Düzenli B12 vitamini kullanıyor."
    cand = MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content=private_text,
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_type=MemorySourceType.EXPLICIT_USER,
    )

    rec = await mem_service.store_candidate(cand)
    assert rec is not None
    assert rec.sensitivity == MemorySensitivity.PRIVATE
    assert rec.encrypted_content is not None

    # Simulate Firestore document structure: plaintext absent
    from integrations.firebase.memory_repository import FirestoreMemoryRepository
    f_repo = FirestoreMemoryRepository(uid="test_uid")
    doc_data = f_repo._record_to_doc(rec)

    assert doc_data["content"] is None
    assert doc_data["embedding"] is None
    assert "encrypted" in doc_data
    assert "ciphertext" in doc_data["encrypted"]
    assert private_text not in json.dumps(doc_data)

    # Local authorized decryption succeeds
    key = await mem_service._key_provider.get_or_create_memory_key()
    decrypted = MemoryEncryptor.decrypt(rec.encrypted_content, key)
    assert decrypted == private_text


# ============================================================================
# SCENARIO M5: Secret Protection
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_m5_secret_protection_never_stores_raw_secret(
    memory_environment: tuple[MemoryService, AgentRuntime, MockLLMAdapter],
) -> None:
    mem_service, _, _ = memory_environment

    raw_input_with_secret = "GitHub erişim tokenım: ghp_123456789012345678901234567890123456 ve şifrem: P@ssw0rd123"
    cand = MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content=raw_input_with_secret,
        confidence=0.99,
        source_type=MemorySourceType.EXPLICIT_USER,
    )

    stored = await mem_service.store_candidate(cand)
    assert stored is not None
    assert stored.sensitivity == MemorySensitivity.SECRET_REFERENCE
    assert stored.embedding is None  # Never embedded!

    # Verify raw secret strings are completely absent
    stored_str = str(stored.model_dump())
    assert "ghp_1234567890" not in stored_str
    assert "P@ssw0rd123" not in stored_str
    assert "[REDACTED_SECRET:" in stored_str


# ============================================================================
# SCENARIOS C1, C2, C3: Cloud Command Execution, Idempotency & Offline Recovery
# ============================================================================
@pytest.mark.asyncio
async def test_scenarios_c1_c2_c3_cloud_command_lifecycle(
    memory_environment: tuple[MemoryService, AgentRuntime, MockLLMAdapter],
) -> None:
    _, runtime, _ = memory_environment
    cmd_repo = InMemoryCommandRepository()

    # Scenario C3: Offline queuing (Command queued while worker is stopped)
    worker = CommandWorker(command_repo=cmd_repo, runtime=runtime)
    # Worker is not running yet

    cmd1 = CloudCommand(
        id="cmd_offline_1",
        type="tool_execution",
        name="system.get_status",
        source_device="ios-app",
        idempotency_key="idemp_c1",
    )
    await cmd_repo.create(cmd1)

    # Verify command is queued offline
    queued_list = await cmd_repo.list(status=CommandStatus.QUEUED)
    assert len(queued_list) == 1

    # Scenario C1: Start worker -> processes queued command
    polled = await worker.poll_once()
    assert polled is not None
    assert polled.id == "cmd_offline_1"
    assert polled.status == CommandStatus.COMPLETED
    assert polled.result["data"]["status"] == "online"

    # Scenario C2: Duplicate command transmission with same idempotency key
    cmd_duplicate = CloudCommand(
        id="cmd_offline_duplicate",
        type="tool_execution",
        name="system.get_status",
        source_device="ios-app",
        idempotency_key="idemp_c1",  # Same idempotency key!
    )
    dup_res = await cmd_repo.create(cmd_duplicate)
    assert dup_res.id == "cmd_offline_1"  # Returns original command, does not re-queue


# ============================================================================
# TEMPORAL CONSISTENCY: Weekday / Date Deterministic Grounding Acceptance
# ============================================================================
@pytest.mark.asyncio
async def test_deterministic_temporal_scheduling_consistency(
    memory_environment: tuple[MemoryService, AgentRuntime, MockLLMAdapter],
) -> None:
    _, runtime, llm = memory_environment
    from zoneinfo import ZoneInfo
    anchor = datetime(2026, 9, 30, 21, 0, 0, tzinfo=ZoneInfo("Europe/Istanbul"))

    # LLM simulates a model hallucinating "Monday" alongside 2026-10-01
    llm.queue_response(LLMResponse(
        content="Based on your preference, I propose scheduling for Tomorrow (Monday, 2026-10-01) from 19:00 to 21:00."
    ))

    run = await runtime.run(
        "Yarın Jarvis projesine iki saat ayırmak istiyorum. Uygun bir zaman önerir misin?",
        anchor_datetime=anchor,
    )
    assert run.state == AgentState.COMPLETED

    # The deterministic temporal guard MUST have sanitized the hallucination
    assert "2026-10-01" in run.final_response
    assert "Thursday" in run.final_response
    assert "Monday" not in run.final_response
    assert "Pazartesi" not in run.final_response
