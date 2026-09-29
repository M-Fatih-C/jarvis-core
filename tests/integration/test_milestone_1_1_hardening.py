"""Milestone 1.1 Core Hardening Acceptance Test Suite.

Verifies:
- TEST 1: Temporal resolution of relative dates ("yarın") anchored against real/injected datetime
- TEST 2: Strict conversation history (Assistant tool_call record -> Tool result with matching tool_call_id)
- TEST 3: Multi-step tool reasoning re-entering cognitive loop after approval
- TEST 4: Tool call syntax corruption triggers repair/retry instead of being treated as plain text
- TEST 5: Concurrency guard serializes MLX inference calls
- TEST 6: Single-flight per-call lock guarantees identical concurrent tool calls execute exactly once
"""

import asyncio
from datetime import datetime, timezone
import pytest
from zoneinfo import ZoneInfo

from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.exceptions import ToolCallParseError
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.config.settings import Settings
from core.llm.base import LLMAdapter
from core.llm.mock_adapter import MockLLMAdapter
from core.llm.schemas import LLMResponse
from core.models.agent import AgentMode
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import RiskLevel, ToolCall, ToolDefinition, ToolResult
from core.policy.engine import PolicyEngine
from core.tools.base import JarvisTool
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry


@pytest.fixture
def hardened_environment() -> tuple[AgentRuntime, MockLLMAdapter, ToolRegistry, ToolExecutor]:
    registry = ToolRegistry()
    register_mock_tools(registry)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    llm = MockLLMAdapter()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        default_timezone="Europe/Istanbul",
        max_retries=2,
    )
    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(timezone_name="Europe/Istanbul"),
        settings=settings,
    )
    return runtime, llm, registry, executor


# ============================================================================
# TEST 1: Temporal Context & Relative Date Calculation
# ============================================================================
@pytest.mark.asyncio
async def test_1_temporal_awareness_relative_date(
    hardened_environment: tuple[AgentRuntime, MockLLMAdapter, ToolRegistry, ToolExecutor],
) -> None:
    """TEST 1:
    'Yarın 19:00'da YBS çalışmayı hatırlat.'
    Given anchor datetime 2026-10-15T10:00:00+03:00,
    the model must resolve 'tomorrow' to 2026-10-16T19:00:00+03:00,
    pause in WAITING_APPROVAL, and upon approval execute tool and complete.
    """
    runtime, _, _, _ = hardened_environment
    anchor_dt = datetime(2026, 10, 15, 10, 0, 0, tzinfo=ZoneInfo("Europe/Istanbul"))

    run = await runtime.run(
        "Yarın 19:00'da YBS çalışmayı hatırlat.",
        anchor_datetime=anchor_dt,
    )

    assert run.state == AgentState.WAITING_APPROVAL
    assert run.pending_tool_call is not None
    assert run.pending_tool_call.name == "reminders.create"

    due_at_arg = run.pending_tool_call.arguments.get("due_at")
    assert due_at_arg is not None
    # Must match tomorrow's date: 2026-10-16T19:00:00
    assert "2026-10-16T19:00:00" in due_at_arg

    # Approve and complete
    approval_id = run.pending_approval_id
    completed_run = await runtime.resume_approval(approval_id)
    assert completed_run.state == AgentState.COMPLETED
    assert completed_run.tool_call_count == 1
    assert "oluşturuldu" in completed_run.final_response


# ============================================================================
# TEST 2: Tool Result History Protocol
# ============================================================================
@pytest.mark.asyncio
async def test_2_tool_conversation_history_protocol(
    hardened_environment: tuple[AgentRuntime, MockLLMAdapter, ToolRegistry, ToolExecutor],
) -> None:
    """TEST 2:
    Conversation history must record:
    1. SYSTEM message
    2. USER message
    3. ASSISTANT message with tool_calls (including tool_call.id)
    4. TOOL message with matching tool_call_id
    5. ASSISTANT final response message
    """
    runtime, _, _, _ = hardened_environment
    run = await runtime.run("Mac'in durumunu kontrol et.")

    assert run.state == AgentState.COMPLETED
    messages = runtime._contexts[run.id]

    # Verify message sequence roles
    roles = [m.role for m in messages]
    assert roles == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]

    assistant_tool_msg = messages[2]
    tool_result_msg = messages[3]

    assert len(assistant_tool_msg.tool_calls) == 1
    call_id = assistant_tool_msg.tool_calls[0].id
    assert tool_result_msg.tool_call_id == call_id
    assert tool_result_msg.tool_name == "system.get_status"
    assert "online" in tool_result_msg.content


# ============================================================================
# TEST 3: Multi-Step Reasoning After Approval
# ============================================================================
@pytest.mark.asyncio
async def test_3_multi_step_agent_loop_after_approval(
    hardened_environment: tuple[AgentRuntime, MockLLMAdapter, ToolRegistry, ToolExecutor],
) -> None:
    """TEST 3:
    Scenario: 'Takvimime bak ve boş saate YBS çalışma reminder'ı koy.'
    1. Step 1: calendar.list_events executes automatically (R0 ALLOW).
    2. Step 2: Model evaluates calendar, proposes reminders.create (R2 REQUIRE_APPROVAL).
       AgentRun pauses in WAITING_APPROVAL.
    3. Step 3: User calls approve.
       Agent does NOT prematurely complete; it resumes into the cognitive loop,
       executes reminders.create, receives result, and produces final response.
    """
    runtime, _, _, _ = hardened_environment

    run = await runtime.run("Takvimime bak ve reminder ekle.")

    # Should pause on second tool (reminders.create)
    assert run.state == AgentState.WAITING_APPROVAL
    assert run.tool_call_count == 1  # calendar.list_events already executed
    assert run.pending_tool_call.name == "reminders.create"

    # User approves second tool
    approval_id = run.pending_approval_id
    completed_run = await runtime.resume_approval(approval_id)

    assert completed_run.state == AgentState.COMPLETED
    assert completed_run.tool_call_count == 2
    assert "oluşturuldu" in completed_run.final_response


# ============================================================================
# TEST 4: Corrupted Tool Output Triggers Repair / Retry Loop
# ============================================================================
@pytest.mark.asyncio
async def test_4_corrupted_tool_output_triggers_repair_loop(
    hardened_environment: tuple[AgentRuntime, MockLLMAdapter, ToolRegistry, ToolExecutor],
) -> None:
    """TEST 4:
    When model outputs malformed tool call syntax, it must NOT be swallowed as plain text.
    The runtime must detect ToolCallParseError and trigger a repair prompt up to max_retries.
    """
    runtime, mock_llm, _, _ = hardened_environment

    # Queue an adapter that raises ToolCallParseError on first attempt, then succeeds on retry
    call_count = 0

    class FlakyMockAdapter(LLMAdapter):
        async def load(self): pass
        async def unload(self): pass
        async def health(self): return True
        async def generate(self, messages):
            return LLMResponse(content="ok")

        async def generate_with_tools(self, messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate malformed XML/JSON tool syntax
                raise ToolCallParseError("Malformed JSON in <tool_call>: {'broken: json")
            return LLMResponse(
                content="Sistem durumu kontrol edildi ve online.",
                tool_calls=[],
                finish_reason="stop",
            )

    runtime._llm = FlakyMockAdapter()

    run = await runtime.run("Mac'in durumunu kontrol et.")

    assert run.state == AgentState.COMPLETED
    assert call_count == 2  # Proves repair retry was initiated
    messages = runtime._contexts[run.id]
    # Check that system notice with repair prompt was injected
    assert any("System notice: Your tool call could not be parsed" in m.content for m in messages)


# ============================================================================
# TEST 5: Concurrency Guard Serializes MLX Inference at Adapter Method Level
# ============================================================================
@pytest.mark.asyncio
async def test_5_concurrency_guard_serializes_inference() -> None:
    """TEST 5:
    Requirement 4: Verify concurrency serialization at the adapter method level.
    Simultaneous calls to adapter.generate() must be serialized through the adapter's
    internal concurrency guard, preventing concurrent calls to the underlying inference engine.
    """
    import time
    from unittest.mock import MagicMock, patch
    from core.llm.mlx_adapter import QwenMLXAdapter

    adapter = QwenMLXAdapter()
    adapter._is_loaded = True
    adapter._model = MagicMock()
    mock_tok = MagicMock()
    mock_tok.apply_chat_template.return_value = "prompt"
    adapter._tokenizer = mock_tok

    active_executions = 0
    max_concurrent_seen = 0

    def fake_mlx_generate(*args, **kwargs):
        nonlocal active_executions, max_concurrent_seen
        active_executions += 1
        max_concurrent_seen = max(max_concurrent_seen, active_executions)
        time.sleep(0.04)  # Simulate GPU inference latency
        active_executions -= 1
        return "Serialized response"

    # Invoke real adapter.generate() concurrently across 3 tasks
    mock_mlx_lm = MagicMock()
    mock_mlx_lm.generate.side_effect = fake_mlx_generate
    mock_mlx_lm.sample_utils.make_sampler.return_value = MagicMock()
    with patch.dict("sys.modules", {"mlx_lm": mock_mlx_lm, "mlx_lm.sample_utils": mock_mlx_lm.sample_utils}):
        results = await asyncio.gather(
            adapter.generate([ChatMessage(role=MessageRole.USER, content="Query 1")]),
            adapter.generate([ChatMessage(role=MessageRole.USER, content="Query 2")]),
            adapter.generate([ChatMessage(role=MessageRole.USER, content="Query 3")]),
        )

    # Concurrency guard inside adapter.generate() must guarantee serialization
    assert max_concurrent_seen == 1
    assert len(results) == 3
    assert all(r.content == "Serialized response" for r in results)


# ============================================================================
# TEST 6: Single-Flight Per-Call Tool Execution Idempotency
# ============================================================================
@pytest.mark.asyncio
async def test_6_single_flight_per_call_idempotency() -> None:
    """TEST 6:
    When two concurrent coroutines execute the exact same tool_call.id simultaneously,
    the underlying tool must execute EXACTLY once.
    """
    execution_counter = 0

    class CounterTool(JarvisTool):
        definition = ToolDefinition(
            name="test.counter",
            description="Increments execution counter",
            risk_level=RiskLevel.R0_READ,
            input_schema={"type": "object"},
            requires_approval=False,
        )
        async def execute(self, arguments):
            nonlocal execution_counter
            await asyncio.sleep(0.05)  # Simulate non-instant execution
            execution_counter += 1
            return ToolResult(tool_call_id="", success=True, data={"count": execution_counter})

    registry = ToolRegistry()
    registry.register(CounterTool())
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)

    call = ToolCall(
        id="concurrent_call_xyz",
        name="test.counter",
        arguments={},
        requested_at=datetime.now(timezone.utc),
    )

    # Launch two simultaneous executions of the same tool_call
    res1, res2 = await asyncio.gather(
        executor.execute_tool_call(call, AgentMode.ASSIST),
        executor.execute_tool_call(call, AgentMode.ASSIST),
    )

    # Underlying tool was executed exactly once
    assert execution_counter == 1
    assert res1.data["count"] == 1
    assert res2.data["count"] == 1
    assert res1 is res2


# ============================================================================
# TEST 7: Parse Retry Counter Scope Resets Per Reasoning Step
# ============================================================================
@pytest.mark.asyncio
async def test_7_parse_retry_counter_resets_per_step(
    hardened_environment: tuple[AgentRuntime, MockLLMAdapter, ToolRegistry, ToolExecutor],
) -> None:
    """TEST 7:
    Requirement 6: Verify that parse_retries is scoped per reasoning step.
    If Step 1 uses 1 retry to succeed, Step 2 must start with a fresh retry budget (0)
    and successfully retry without exceeding max_retries=2.
    """
    runtime, _, _, _ = hardened_environment

    step1_attempts = 0
    step2_attempts = 0

    class MultiStepFlakyAdapter(LLMAdapter):
        async def load(self): pass
        async def unload(self): pass
        async def health(self): return True
        async def generate(self, messages):
            return LLMResponse(content="Final completion")

        async def generate_with_tools(self, messages, tools):
            nonlocal step1_attempts, step2_attempts
            # Check if this is Step 1 (no tool result yet) or Step 2 (calendar result present)
            has_calendar_result = any(m.role == MessageRole.TOOL and "online" in m.content for m in messages)

            if not has_calendar_result:
                step1_attempts += 1
                if step1_attempts == 1:
                    # Fail step 1 first time
                    raise ToolCallParseError("Step 1 syntax error")
                # Succeed step 1 second time
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="call_step1", name="system.get_status", arguments={})],
                    finish_reason="tool_calls",
                )
            else:
                step2_attempts += 1
                if step2_attempts == 1:
                    # Fail step 2 first time
                    raise ToolCallParseError("Step 2 syntax error")
                # Succeed step 2 second time
                return LLMResponse(
                    content="İki adım da başarıyla tamamlandı.",
                    tool_calls=[],
                    finish_reason="stop",
                )

    runtime._llm = MultiStepFlakyAdapter()

    run = await runtime.run("Mac'in durumunu kontrol et.")

    assert run.state == AgentState.COMPLETED
    assert step1_attempts == 2  # 1 failure + 1 success
    assert step2_attempts == 2  # 1 failure + 1 success
    assert run.final_response == "İki adım da başarıyla tamamlandı."
