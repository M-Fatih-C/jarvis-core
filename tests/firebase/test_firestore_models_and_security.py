"""Tests for Firestore data mapping, security invariants, and security rules."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pytest
from uuid import uuid4

from core.config.settings import Settings
from core.memory.crypto import InMemoryKeyProvider, MemoryEncryptor
from core.memory.models import (
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from integrations.firebase.memory_repository import FirestoreMemoryRepository


@pytest.fixture
def firestore_repo() -> FirestoreMemoryRepository:
    settings = Settings(firebase_project_id="test-project", jarvis_uid="user_abc123")
    return FirestoreMemoryRepository(client_provider=None, uid="user_abc123", settings=settings)


def test_normal_memory_firestore_mapping(firestore_repo: FirestoreMemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User prefers working after 18:00",
        structured={"after": "18:00"},
        confidence=0.95,
        importance=0.8,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp1",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=[0.1, 0.2, 0.3],
        embedding_model="test-embedder",
    )
    doc = firestore_repo._record_to_doc(rec)

    assert doc["kind"] == "preference"
    assert doc["sensitivity"] == "normal"
    assert doc["content"] == "User prefers working after 18:00"
    assert doc["embedding"] == [0.1, 0.2, 0.3]
    assert doc["status"] == "active"
    assert "encrypted" not in doc


def test_private_memory_firestore_mapping_strips_plaintext_and_embedding(
    firestore_repo: FirestoreMemoryRepository,
) -> None:
    now = datetime.now(timezone.utc)
    enc = {"ciphertext": "c2VjcmV0...", "nonce": "bm9uY2U...", "algorithm": "AES-256-GCM", "key_version": 1}
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROFILE,
        sensitivity=MemorySensitivity.PRIVATE,
        content=None,
        encrypted_content=json.dumps(enc),
        structured={"confidential": True},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_private",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=None,
    )
    doc = firestore_repo._record_to_doc(rec)

    # Security invariant 5: PRIVATE never exists as plaintext in Firestore
    assert doc["content"] is None
    # Security invariant 6: PRIVATE cloud embeddings are forbidden
    assert doc["embedding"] is None
    assert doc["embedding_model"] is None
    assert doc["encrypted"]["ciphertext"] == "c2VjcmV0..."


def test_local_only_memory_rejected_from_firestore(firestore_repo: FirestoreMemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROJECT,
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        content="This should never reach the cloud",
        structured={},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_local",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )

    # Security invariant 4: LOCAL_ONLY never syncs to cloud
    with pytest.raises(ValueError, match="LOCAL_ONLY memory records must never be saved to Firestore"):
        firestore_repo._record_to_doc(rec)


def test_secret_reference_memory_has_no_embedding(firestore_repo: FirestoreMemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROFILE,
        sensitivity=MemorySensitivity.SECRET_REFERENCE,
        content="github_main",
        structured={"service": "github"},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_sec",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=None,
    )
    doc = firestore_repo._record_to_doc(rec)
    assert doc["content"] == "github_main"
    assert doc["embedding"] is None


def test_firestore_security_rules_file_exists_and_enforces_user_isolation() -> None:
    rules_path = Path("cloud/firestore.rules")
    assert rules_path.exists(), "firestore.rules file must exist"
    content = rules_path.read_text(encoding="utf-8")

    # Verify user-level isolation rules
    assert "match /users/{userId}" in content
    assert "request.auth != null" in content
    assert "request.auth.uid == userId" in content

    # Verify client cannot modify worker-owned fields
    assert "lease_owner" in content
    assert "result" in content
    assert "error" in content
    assert "allow delete: if false" in content


@pytest.mark.asyncio
async def test_client_supplied_is_approved_flag_cannot_execute_r2_tool() -> None:
    """Invariant: A client passing is_approved=True in payload must NEVER execute an R2/R4 tool without server approval."""
    from core.cloud.command_worker import CommandWorker
    from core.cloud.models import CloudCommand, CommandStatus
    from core.cloud.repositories import InMemoryCommandRepository
    from core.models.agent import AgentMode
    from core.models.tools import RiskLevel, ToolDefinition, ToolResult
    from core.tools.base import JarvisTool
    from unittest.mock import AsyncMock, MagicMock

    # Setup mock runtime and tool
    mock_runtime = MagicMock()
    mock_runtime._settings.default_agent_mode = "assist"
    mock_tool = MagicMock()
    mock_tool.definition = ToolDefinition(
        name="calendar.create_event",
        description="Create event",
        risk_level=RiskLevel.R2_WRITE,
        input_schema={},
        requires_approval=True,
    )
    mock_runtime._tools.get.return_value = mock_tool

    repo = InMemoryCommandRepository()
    worker = CommandWorker(command_repo=repo, runtime=mock_runtime)

    cmd = CloudCommand(
        id="cmd_exploit_01",
        type="tool_execution",
        name="calendar.create_event",
        idempotency_key="idem_01",
        source_device="iphone_attacker",
        payload={"arguments": {"title": "Secret Event"}, "is_approved": True},  # Attacker attempt
        status=CommandStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    await repo.create(cmd)

    processed = await worker.poll_once()
    assert processed is not None
    assert processed.status == CommandStatus.FAILED
    assert "ApprovalRequired" in processed.error
    assert "is_approved=true is rejected" in processed.error


@pytest.mark.asyncio
async def test_tampered_action_digest_rejected_by_command_worker() -> None:
    """Invariant: If tool arguments change after approval was granted, action digest mismatch rejects execution."""
    from core.agent.approval_store import ApprovalStore
    from core.cloud.command_worker import CommandWorker
    from core.cloud.models import CloudCommand, CommandStatus
    from core.cloud.repositories import InMemoryCommandRepository
    from core.models.tools import RiskLevel, ToolCall, ToolDefinition
    from unittest.mock import MagicMock

    approval_store = ApprovalStore()
    mock_runtime = MagicMock()
    mock_runtime._settings.default_agent_mode = "assist"
    mock_runtime.approval_store = approval_store

    mock_tool = MagicMock()
    mock_tool.definition = ToolDefinition(
        name="calendar.create_event",
        description="Create event",
        risk_level=RiskLevel.R2_WRITE,
        input_schema={},
        requires_approval=True,
    )
    mock_runtime._tools.get.return_value = mock_tool

    # Create server approval for "Original Meeting"
    run_id = uuid4()
    original_call = ToolCall(id="call_123", name="calendar.create_event", arguments={"title": "Original Meeting"})
    app_req = approval_store.create(agent_run_id=run_id, tool_call=original_call)

    repo = InMemoryCommandRepository()
    worker = CommandWorker(command_repo=repo, runtime=mock_runtime)

    # Attacker tries to use approval ID with tampered arguments ("Malicious Meeting")
    cmd = CloudCommand(
        id="cmd_tamper_01",
        type="tool_execution",
        name="calendar.create_event",
        idempotency_key="idem_02",
        source_device="iphone",
        payload={
            "approval_id": str(app_req.id),
            "tool_call_id": "call_123",
            "arguments": {"title": "Malicious Meeting"},
        },
        status=CommandStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    await repo.create(cmd)

    processed = await worker.poll_once()
    assert processed is not None
    assert processed.status == CommandStatus.FAILED
    assert "ActionDigestMismatch" in processed.error


@pytest.mark.asyncio
async def test_expired_approval_rejected_by_command_worker() -> None:
    """Invariant: An expired approval cannot be executed."""
    from core.agent.approval_store import ApprovalStore
    from core.cloud.command_worker import CommandWorker
    from core.cloud.models import CloudCommand, CommandStatus
    from core.cloud.repositories import InMemoryCommandRepository
    from core.models.tools import RiskLevel, ToolCall, ToolDefinition
    from unittest.mock import MagicMock

    approval_store = ApprovalStore(default_ttl_seconds=-10)  # already expired
    mock_runtime = MagicMock()
    mock_runtime._settings.default_agent_mode = "assist"
    mock_runtime.approval_store = approval_store

    mock_tool = MagicMock()
    mock_tool.definition = ToolDefinition(
        name="calendar.create_event",
        description="Create event",
        risk_level=RiskLevel.R2_WRITE,
        input_schema={},
        requires_approval=True,
    )
    mock_runtime._tools.get.return_value = mock_tool

    run_id = uuid4()
    call = ToolCall(id="call_exp", name="calendar.create_event", arguments={"title": "Old"})
    app_req = approval_store.create(agent_run_id=run_id, tool_call=call, ttl_seconds=-10)

    repo = InMemoryCommandRepository()
    worker = CommandWorker(command_repo=repo, runtime=mock_runtime)

    cmd = CloudCommand(
        id="cmd_exp_01",
        type="tool_execution",
        name="calendar.create_event",
        idempotency_key="idem_03",
        source_device="iphone",
        payload={
            "approval_id": str(app_req.id),
            "tool_call_id": "call_exp",
            "arguments": {"title": "Old"},
        },
        status=CommandStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    await repo.create(cmd)

    processed = await worker.poll_once()
    assert processed is not None
    assert processed.status == CommandStatus.FAILED
    assert "ApprovalExpired" in processed.error


@pytest.mark.asyncio
async def test_consumed_approval_cannot_be_reused() -> None:
    """Invariant: An approval can only be consumed once."""
    from core.agent.approval_store import ApprovalStore
    from core.cloud.command_worker import CommandWorker
    from core.cloud.models import CloudCommand, CommandStatus
    from core.cloud.repositories import InMemoryCommandRepository
    from core.models.tools import RiskLevel, ToolCall, ToolDefinition, ToolResult
    from unittest.mock import AsyncMock, MagicMock

    approval_store = ApprovalStore()
    mock_runtime = MagicMock()
    mock_runtime._settings.default_agent_mode = "assist"
    mock_runtime.approval_store = approval_store

    mock_tool = MagicMock()
    mock_tool.definition = ToolDefinition(
        name="calendar.create_event",
        description="Create event",
        risk_level=RiskLevel.R2_WRITE,
        input_schema={},
        requires_approval=True,
    )
    mock_runtime._tools.get.return_value = mock_tool

    mock_executor = MagicMock()
    mock_executor.execute_tool_call = AsyncMock(return_value=ToolResult(tool_call_id="c1", success=True, data={"id": "evt1"}))

    run_id = uuid4()
    call = ToolCall(id="c1", name="calendar.create_event", arguments={"title": "Once"})
    app_req = approval_store.create(agent_run_id=run_id, tool_call=call)

    repo = InMemoryCommandRepository()
    worker = CommandWorker(command_repo=repo, runtime=mock_runtime, tool_executor=mock_executor)

    # First execution succeeds
    cmd1 = CloudCommand(
        id="cmd_use_1",
        type="tool_execution",
        name="calendar.create_event",
        idempotency_key="idem_use_1",
        source_device="iphone",
        payload={
            "approval_id": str(app_req.id),
            "tool_call_id": "c1",
            "arguments": {"title": "Once"},
        },
        status=CommandStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    await repo.create(cmd1)
    res1 = await worker.poll_once()
    assert res1.status == CommandStatus.COMPLETED

    # Second execution attempt with same approval fails
    cmd2 = CloudCommand(
        id="cmd_use_2",
        type="tool_execution",
        name="calendar.create_event",
        idempotency_key="idem_use_2",
        source_device="iphone",
        payload={
            "approval_id": str(app_req.id),
            "tool_call_id": "c1",
            "arguments": {"title": "Once"},
        },
        status=CommandStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    await repo.create(cmd2)
    res2 = await worker.poll_once()
    assert res2.status == CommandStatus.FAILED
    assert "ApprovalAlreadyConsumed" in res2.error


@pytest.mark.asyncio
async def test_r5_tool_strictly_denied_in_command_worker() -> None:
    """Invariant: R5_SENSITIVE tools are unconditionally denied in CommandWorker."""
    from core.cloud.command_worker import CommandWorker
    from core.cloud.models import CloudCommand, CommandStatus
    from core.cloud.repositories import InMemoryCommandRepository
    from core.models.tools import RiskLevel, ToolDefinition
    from unittest.mock import MagicMock

    mock_runtime = MagicMock()
    mock_runtime._settings.default_agent_mode = "assist"
    mock_tool = MagicMock()
    mock_tool.definition = ToolDefinition(
        name="security.export_private_keys",
        description="Export keys",
        risk_level=RiskLevel.R5_SENSITIVE,
        input_schema={},
    )
    mock_runtime._tools.get.return_value = mock_tool

    repo = InMemoryCommandRepository()
    worker = CommandWorker(command_repo=repo, runtime=mock_runtime)

    cmd = CloudCommand(
        id="cmd_r5",
        type="tool_execution",
        name="security.export_private_keys",
        idempotency_key="idem_r5",
        source_device="iphone",
        payload={"arguments": {}},
        status=CommandStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    await repo.create(cmd)
    res = await worker.poll_once()
    assert res.status == CommandStatus.FAILED
    assert "PolicyDenied" in res.error
    assert "R5_SENSITIVE" in res.error

