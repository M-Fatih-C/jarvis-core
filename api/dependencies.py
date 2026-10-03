"""FastAPI application dependencies and service container."""

from functools import lru_cache
from typing import Any, Optional
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.config.settings import Settings, get_settings
from core.llm.base import LLMAdapter
from core.llm.mlx_adapter import QwenMLXAdapter
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.registry import ToolRegistry

_custom_llm_adapter: Optional[LLMAdapter] = None
_custom_runtime: Optional[AgentRuntime] = None
_custom_memory_service: Optional[Any] = None


def set_custom_llm_adapter(adapter: Optional[LLMAdapter]) -> None:
    """Override default LLM adapter (useful for testing)."""
    global _custom_llm_adapter, _custom_runtime
    _custom_llm_adapter = adapter
    _custom_runtime = None  # Reset runtime to pick up new adapter


def set_custom_memory_service(service: Optional[Any]) -> None:
    """Override default memory service (useful for testing)."""
    global _custom_memory_service, _custom_runtime
    _custom_memory_service = service
    _custom_runtime = None


def set_custom_runtime(runtime: Optional[AgentRuntime]) -> None:
    """Override runtime completely for mock testing."""
    global _custom_runtime
    _custom_runtime = runtime


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """Create and populate standard tool registry."""
    registry = ToolRegistry()
    from core.tools.native_macos.provider import register_apple_tools
    from core.tools.memory_tools import MemorySearchTool
    settings = get_settings()
    register_apple_tools(registry, provider=settings.apple_integration_provider)
    if settings.apple_integration_provider == "native_macos":
        registry.register(MemorySearchTool(get_memory_service()))
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


def get_memory_service() -> Any:
    """Return active MemoryService singleton."""
    global _custom_memory_service
    if _custom_memory_service is not None:
        return _custom_memory_service
    return _get_default_memory_service()


@lru_cache(maxsize=1)
def _get_default_memory_service() -> Any:
    from core.memory.service import MemoryService
    return MemoryService()


_custom_approval_store: Optional[ApprovalStore] = None
_custom_email_storage: Optional[Any] = None
_custom_bridge_client: Optional[Any] = None


def set_custom_approval_store(store: Optional[ApprovalStore]) -> None:
    """Override default approval store (useful for testing)."""
    global _custom_approval_store
    _custom_approval_store = store


def set_custom_email_storage(storage: Optional[Any]) -> None:
    """Override default email storage (useful for testing)."""
    global _custom_email_storage
    _custom_email_storage = storage


def set_custom_bridge_client(client: Optional[Any]) -> None:
    """Override default mac bridge client (useful for testing)."""
    global _custom_bridge_client
    _custom_bridge_client = client


@lru_cache(maxsize=1)
def get_approval_store() -> ApprovalStore:
    """Return active ApprovalStore singleton."""
    global _custom_approval_store
    if _custom_approval_store is not None:
        return _custom_approval_store
    return ApprovalStore()


def get_mac_bridge_client() -> Any:
    """Return active MacBridgeClient singleton."""
    global _custom_bridge_client
    if _custom_bridge_client is not None:
        return _custom_bridge_client
    return _get_default_bridge_client()


@lru_cache(maxsize=1)
def _get_default_bridge_client() -> Any:
    from integrations.macos.client import MacBridgeClient
    return MacBridgeClient()


def get_email_storage() -> Any:
    """Return active EmailStorage singleton."""
    global _custom_email_storage
    if _custom_email_storage is not None:
        return _custom_email_storage
    return _get_default_email_storage()


@lru_cache(maxsize=1)
def _get_default_email_storage() -> Any:
    from integrations.gmail.storage import EmailStorage
    settings = get_settings()
    return EmailStorage(db_path=settings.email_db_path)


def get_calendar_manager() -> Any:
    """Return active CalendarTargetManager singleton."""
    from core.task_planning.calendar_manager import CalendarTargetManager
    return CalendarTargetManager(
        bridge_client=get_mac_bridge_client(),
        settings=get_settings(),
    )


def get_task_planner() -> Any:
    """Return active TaskPlanner singleton."""
    from core.task_planning.planner import TaskPlanner
    settings = get_settings()
    buffer_mins = getattr(settings, "planning_buffer_minutes", 15)
    return TaskPlanner(
        bridge_client=get_mac_bridge_client(),
        buffer_minutes=buffer_mins,
    )


def get_task_approval_service() -> Any:
    """Return active TaskApprovalService singleton."""
    from core.task_planning.approval_service import TaskApprovalService
    return TaskApprovalService(
        storage=get_email_storage(),
        policy_engine=get_policy_engine(),
        approval_store=get_approval_store(),
        bridge_client=get_mac_bridge_client(),
    )


def get_agent_runtime() -> AgentRuntime:
    """Return AgentRuntime singleton."""
    global _custom_runtime
    if _custom_runtime is not None:
        return _custom_runtime
    return _get_default_runtime()


@lru_cache(maxsize=1)
def _get_default_runtime() -> AgentRuntime:
    from pathlib import Path
    from core.agent.conversation_store import ConversationStore
    settings = get_settings()
    history = ConversationStore(Path(settings.memory_db_path).expanduser().with_name("conversations.db")) if settings.environment == "production" else None
    return AgentRuntime(
        llm_adapter=get_llm_adapter(),
        tool_registry=get_tool_registry(),
        policy_engine=get_policy_engine(),
        tool_executor=get_tool_executor(),
        approval_store=get_approval_store(),
        context_builder=ContextBuilder(),
        memory_service=get_memory_service(),
        settings=settings,
        conversation_store=history,
    )

@lru_cache(maxsize=1)
def get_gmail_pipeline() -> Any:
    from core.email_analysis.analyzer import EmailAnalyzer
    from core.email_analysis.task_extractor import TaskExtractor
    from core.notifications.email_notifier import EmailNotificationService
    from integrations.gmail.auth import GmailOAuthManager
    from integrations.gmail.client import GmailClient
    from integrations.gmail.sync import GmailSyncService
    from integrations.gmail.pipeline import GmailProcessingPipeline
    settings = get_settings()
    storage = get_email_storage()
    return GmailProcessingPipeline(
        sync_service=GmailSyncService(GmailClient(GmailOAuthManager(settings=settings)), storage,
                                     lookback_days=settings.gmail_sync_lookback_days,
                                     max_messages_per_sync=settings.gmail_max_sync_messages),
        analyzer=EmailAnalyzer(get_llm_adapter(), settings=settings, memory_service=get_memory_service()),
        task_extractor=TaskExtractor(),
        notifier=EmailNotificationService(storage, get_mac_bridge_client()), storage=storage,
    )
