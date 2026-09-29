"""Unit tests for ToolExecutor including policy checks, idempotency, and argument validation."""

from datetime import datetime, timezone
import pytest
from core.agent.exceptions import ApprovalRequiredError, PolicyDeniedError
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall, ToolDefinition, ToolResult
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock.calendar import ListEventsMockTool
from core.tools.mock.reminders import CreateReminderMockTool
from core.tools.mock.system import SystemStatusMockTool
from core.tools.registry import ToolRegistry
from tests.fixtures.tools import DummySensitiveTool


@pytest.fixture
def test_setup() -> tuple[ToolExecutor, ToolRegistry, PolicyEngine]:
    registry = ToolRegistry()
    registry.register(SystemStatusMockTool())
    registry.register(CreateReminderMockTool())
    registry.register(DummySensitiveTool())
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    return executor, registry, policy


@pytest.mark.asyncio
async def test_executor_allow_success(test_setup: tuple[ToolExecutor, ToolRegistry, PolicyEngine]) -> None:
    """R0 tool executes directly under ASSIST mode."""
    executor, _, _ = test_setup
    call = ToolCall(
        id="call_status_1",
        name="system.get_status",
        arguments={},
        requested_at=datetime.now(timezone.utc),
    )
    result = await executor.execute_tool_call(call, AgentMode.ASSIST, is_approved=False)

    assert result.success is True
    assert result.data == {"platform": "macOS", "status": "online"}
    assert result.tool_call_id == "call_status_1"


@pytest.mark.asyncio
async def test_executor_requires_approval_unapproved_raises(test_setup: tuple[ToolExecutor, ToolRegistry, PolicyEngine]) -> None:
    """R2 tool raises ApprovalRequiredError if is_approved is False."""
    executor, _, _ = test_setup
    call = ToolCall(
        id="call_remind_1",
        name="reminders.create",
        arguments={"title": "YBS çalış", "due_at": "2026-09-30T19:00:00+03:00"},
        requested_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ApprovalRequiredError, match="requires user approval"):
        await executor.execute_tool_call(call, AgentMode.ASSIST, is_approved=False)


@pytest.mark.asyncio
async def test_executor_requires_approval_approved_executes(test_setup: tuple[ToolExecutor, ToolRegistry, PolicyEngine]) -> None:
    """R2 tool executes when is_approved is True."""
    executor, _, _ = test_setup
    call = ToolCall(
        id="call_remind_2",
        name="reminders.create",
        arguments={"title": "YBS çalış", "due_at": "2026-09-30T19:00:00+03:00"},
        requested_at=datetime.now(timezone.utc),
    )
    result = await executor.execute_tool_call(call, AgentMode.ASSIST, is_approved=True)

    assert result.success is True
    assert result.data["status"] == "created"


@pytest.mark.asyncio
async def test_executor_sensitive_r5_denied(test_setup: tuple[ToolExecutor, ToolRegistry, PolicyEngine]) -> None:
    """R5 tool is blocked with PolicyDeniedError even if is_approved is True."""
    executor, _, _ = test_setup
    call = ToolCall(
        id="call_secret_1",
        name="security.export_passwords",
        arguments={"query": "admin"},
        requested_at=datetime.now(timezone.utc),
    )
    with pytest.raises(PolicyDeniedError, match="denied by policy"):
        await executor.execute_tool_call(call, AgentMode.ASSIST, is_approved=True)


@pytest.mark.asyncio
async def test_executor_argument_validation_failure(test_setup: tuple[ToolExecutor, ToolRegistry, PolicyEngine]) -> None:
    """Invalid arguments produce a failed ToolResult rather than crashing."""
    executor, _, _ = test_setup
    # reminders.create requires valid ISO datetime string in due_at
    call = ToolCall(
        id="call_bad_args",
        name="reminders.create",
        arguments={"title": "Test", "due_at": "not-a-datetime"},
        requested_at=datetime.now(timezone.utc),
    )
    result = await executor.execute_tool_call(call, AgentMode.ASSIST, is_approved=True)

    assert result.success is False
    assert "Argument validation failed" in result.error


@pytest.mark.asyncio
async def test_executor_idempotency(test_setup: tuple[ToolExecutor, ToolRegistry, PolicyEngine]) -> None:
    """Duplicate execution of same tool_call_id returns cached result."""
    executor, _, _ = test_setup
    call = ToolCall(
        id="call_idempotent_1",
        name="system.get_status",
        arguments={},
        requested_at=datetime.now(timezone.utc),
    )
    result1 = await executor.execute_tool_call(call, AgentMode.ASSIST)
    result2 = await executor.execute_tool_call(call, AgentMode.ASSIST)

    assert result1 is result2
