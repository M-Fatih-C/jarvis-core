"""Integration tests for Scenario A: Agent tool execution flow with Policy ALLOW."""

import pytest
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.config.settings import Settings
from core.llm.mock_adapter import MockLLMAdapter
from core.models.agent import AgentMode
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry


@pytest.fixture
def agent_environment() -> AgentRuntime:
    registry = ToolRegistry()
    register_mock_tools(registry)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    llm = MockLLMAdapter()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
    )
    return AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_scenario_a_system_status(agent_environment: AgentRuntime) -> None:
    """Scenario A:
    Input: Mac'in durumunu kontrol et.
    Expected:
      Qwen (Mock) → system.get_status
      Policy ALLOW (R0)
      Mock Tool execution
      Qwen → final response
      AgentRun state: COMPLETED
    """
    runtime = agent_environment
    run = await runtime.run("Mac'in durumunu kontrol et.", source="chat")

    assert run.state == AgentState.COMPLETED
    assert run.tool_call_count == 1
    assert run.step_count >= 1
    assert "macOS" in run.final_response
    assert "online" in run.final_response
    assert run.pending_approval_id is None


@pytest.mark.asyncio
async def test_general_chat_no_tools(agent_environment: AgentRuntime) -> None:
    """Conversational query without tool requirements:
    Input: Merhaba Jarvis.
    Expected: Direct response, no tool calls, state COMPLETED.
    """
    runtime = agent_environment
    run = await runtime.run("Merhaba Jarvis.", source="chat")

    assert run.state == AgentState.COMPLETED
    assert run.tool_call_count == 0
    assert run.final_response is not None
    assert len(run.final_response) > 0
