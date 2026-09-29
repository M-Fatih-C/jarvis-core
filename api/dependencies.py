"""FastAPI application dependencies and service container."""

from functools import lru_cache
from typing import Optional
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.config.settings import Settings, get_settings
from core.llm.base import LLMAdapter
from core.llm.mlx_adapter import QwenMLXAdapter
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry

_custom_llm_adapter: Optional[LLMAdapter] = None
_custom_runtime: Optional[AgentRuntime] = None


def set_custom_llm_adapter(adapter: Optional[LLMAdapter]) -> None:
    """Override default LLM adapter (useful for testing)."""
    global _custom_llm_adapter, _custom_runtime
    _custom_llm_adapter = adapter
    _custom_runtime = None  # Reset runtime to pick up new adapter


def set_custom_runtime(runtime: Optional[AgentRuntime]) -> None:
    """Override runtime completely for mock testing."""
    global _custom_runtime
    _custom_runtime = runtime


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """Create and populate standard tool registry."""
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


@lru_cache(maxsize=1)
def get_policy_engine() -> PolicyEngine:
    """Create and cache PolicyEngine singleton."""
    return PolicyEngine()


@lru_cache(maxsize=1)
def get_tool_executor() -> ToolExecutor:
    """Create and cache ToolExecutor singleton."""
    return ToolExecutor(
        registry=get_tool_registry(),
        policy_engine=get_policy_engine(),
    )


def get_llm_adapter() -> LLMAdapter:
    """Return active LLM adapter singleton."""
    global _custom_llm_adapter
    if _custom_llm_adapter is not None:
        return _custom_llm_adapter
    return _get_default_mlx_adapter()


@lru_cache(maxsize=1)
def _get_default_mlx_adapter() -> LLMAdapter:
    settings = get_settings()
    return QwenMLXAdapter(settings=settings)


def get_agent_runtime() -> AgentRuntime:
    """Return AgentRuntime singleton."""
    global _custom_runtime
    if _custom_runtime is not None:
        return _custom_runtime
    return _get_default_runtime()


@lru_cache(maxsize=1)
def _get_default_runtime() -> AgentRuntime:
    return AgentRuntime(
        llm_adapter=get_llm_adapter(),
        tool_registry=get_tool_registry(),
        policy_engine=get_policy_engine(),
        tool_executor=get_tool_executor(),
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        settings=get_settings(),
    )
