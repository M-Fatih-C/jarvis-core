"""Unit tests for ApprovalStore lifecycle and expiration."""

from datetime import datetime, timezone
import time
from uuid import uuid4
import pytest
from core.agent.approval_store import ApprovalStore
from core.agent.exceptions import ApprovalExpiredError, JarvisError
from core.models.approval import ApprovalStatus
from core.models.tools import ToolCall


def make_tool_call() -> ToolCall:
    return ToolCall(
        id="call_test_1",
        name="reminders.create",
        arguments={"title": "Test", "due_at": "2026-10-01T10:00:00+03:00"},
        requested_at=datetime.now(timezone.utc),
    )


def test_approval_lifecycle_approve() -> None:
    store = ApprovalStore(default_ttl_seconds=60)
    run_id = uuid4()
    call = make_tool_call()

    req = store.create(agent_run_id=run_id, tool_call=call)
    assert req.status == ApprovalStatus.PENDING

    approved = store.approve(req.id)
    assert approved.status == ApprovalStatus.APPROVED

    # Subsequent approval attempts raise JarvisError
    with pytest.raises(JarvisError):
        store.approve(req.id)


def test_approval_lifecycle_reject() -> None:
    store = ApprovalStore(default_ttl_seconds=60)
    run_id = uuid4()
    call = make_tool_call()

    req = store.create(agent_run_id=run_id, tool_call=call)
    rejected = store.reject(req.id)
    assert rejected.status == ApprovalStatus.REJECTED


def test_approval_expired() -> None:
    # TTL of 0 seconds causes immediate expiration
    store = ApprovalStore(default_ttl_seconds=0)
    run_id = uuid4()
    call = make_tool_call()

    req = store.create(agent_run_id=run_id, tool_call=call, ttl_seconds=0)
    time.sleep(0.01)

    with pytest.raises(ApprovalExpiredError):
        store.approve(req.id)
