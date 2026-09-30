"""Unit tests for approval action digest integrity binding (Milestone 3, Spec item 18)."""

from datetime import datetime, timezone
from uuid import uuid4
import pytest
from core.agent.approval_store import ApprovalStore
from core.agent.exceptions import ApprovalIntegrityError, JarvisError
from core.models.approval import ApprovalStatus, compute_action_digest
from core.models.tools import ToolCall


def test_action_digest_determinism() -> None:
    run_id = uuid4()
    call_id = "call_abc_123"
    tool_name = "calendar.create_event"

    # Same keys in different order should produce the exact same canonical digest
    args_1 = {"title": "Team Sync", "start": "2026-10-01T10:00:00+03:00", "end": "2026-10-01T11:00:00+03:00"}
    args_2 = {"end": "2026-10-01T11:00:00+03:00", "title": "Team Sync", "start": "2026-10-01T10:00:00+03:00"}

    digest_1 = compute_action_digest(run_id, call_id, tool_name, args_1)
    digest_2 = compute_action_digest(run_id, call_id, tool_name, args_2)

    assert digest_1 == digest_2
    assert len(digest_1) == 64  # SHA-256 hex string


def test_action_digest_sensitivity() -> None:
    run_id = uuid4()
    call_id = "call_abc_123"
    tool_name = "calendar.create_event"
    args = {"title": "Team Sync"}

    base_digest = compute_action_digest(run_id, call_id, tool_name, args)

    # Different arguments
    diff_args = compute_action_digest(run_id, call_id, tool_name, {"title": "Different Title"})
    assert diff_args != base_digest

    # Different tool name
    diff_tool = compute_action_digest(run_id, call_id, "calendar.delete_event", args)
    assert diff_tool != base_digest

    # Different run ID
    diff_run = compute_action_digest(uuid4(), call_id, tool_name, args)
    assert diff_run != base_digest

    # Different call ID
    diff_call = compute_action_digest(run_id, "call_xyz_999", tool_name, args)
    assert diff_call != base_digest


def test_approval_store_action_digest_binding() -> None:
    store = ApprovalStore(default_ttl_seconds=300)
    run_id = uuid4()
    call = ToolCall(
        id="call_reminders_1",
        name="reminders.create",
        arguments={"title": "Buy groceries", "priority": 1},
        requested_at=datetime.now(timezone.utc),
    )

    req = store.create(agent_run_id=run_id, tool_call=call)
    assert req.action_digest is not None
    assert req.consumed_at is None

    expected_digest = compute_action_digest(run_id, call.id, call.name, call.arguments)
    assert req.action_digest == expected_digest

    # Normal approval succeeds
    approved = store.approve(req.id, current_tool_call=call)
    assert approved.status == ApprovalStatus.APPROVED


def test_approval_store_rejects_tampered_call_at_approval() -> None:
    store = ApprovalStore(default_ttl_seconds=300)
    run_id = uuid4()
    call = ToolCall(
        id="call_reminders_1",
        name="reminders.create",
        arguments={"title": "Buy groceries", "priority": 1},
        requested_at=datetime.now(timezone.utc),
    )

    req = store.create(agent_run_id=run_id, tool_call=call)

    # Tampered tool call
    tampered_call = ToolCall(
        id=call.id,
        name=call.name,
        arguments={"title": "Malicious payload", "priority": 9},
        requested_at=call.requested_at,
    )

    with pytest.raises(ApprovalIntegrityError, match="Action digest mismatch"):
        store.approve(req.id, current_tool_call=tampered_call)


def test_approval_store_one_time_consumption() -> None:
    store = ApprovalStore(default_ttl_seconds=300)
    run_id = uuid4()
    call = ToolCall(
        id="call_reminders_1",
        name="reminders.create",
        arguments={"title": "Buy groceries"},
        requested_at=datetime.now(timezone.utc),
    )

    req = store.create(agent_run_id=run_id, tool_call=call)
    store.approve(req.id, current_tool_call=call)

    # First consumption succeeds
    consumed = store.consume(req.id, tool_call=call)
    assert consumed.consumed_at is not None
    assert consumed.consumed_at.tzinfo is not None

    # Second consumption of the same approval MUST fail
    with pytest.raises(ApprovalIntegrityError, match="has already been consumed"):
        store.consume(req.id, tool_call=call)


def test_approval_store_rejects_tampered_call_at_consumption() -> None:
    store = ApprovalStore(default_ttl_seconds=300)
    run_id = uuid4()
    call = ToolCall(
        id="call_reminders_1",
        name="reminders.create",
        arguments={"title": "Legitimate Title"},
        requested_at=datetime.now(timezone.utc),
    )

    req = store.create(agent_run_id=run_id, tool_call=call)
    store.approve(req.id, current_tool_call=call)

    # Tampered at execution time
    tampered_call = ToolCall(
        id=call.id,
        name=call.name,
        arguments={"title": "Altered at runtime"},
        requested_at=call.requested_at,
    )

    with pytest.raises(ApprovalIntegrityError, match="Arguments were altered after approval was granted"):
        store.consume(req.id, tool_call=tampered_call)
