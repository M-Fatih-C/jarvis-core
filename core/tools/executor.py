"""Tool executor with policy enforcement, thread-safe single-flight idempotency, and argument validation."""

import asyncio
from typing import Any
from pydantic import ValidationError
from core.agent.exceptions import (
    ApprovalRequiredError,
    PolicyDeniedError,
    ToolExecutionError,
    ToolNotFoundError,
)
from core.logging.setup import get_logger
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall, ToolResult
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType, PolicyRequest
from core.tools.registry import ToolRegistry

logger = get_logger("jarvis.executor")


class ToolExecutor:
    """Executes tools strictly gated by the Policy Engine with single-flight idempotency guarantees."""

    def __init__(self, registry: ToolRegistry, policy_engine: PolicyEngine) -> None:
        self._registry = registry
        self._policy_engine = policy_engine
        self._executed_cache: dict[str, ToolResult] = {}
        self._global_lock = asyncio.Lock()
        self._in_flight_locks: dict[str, asyncio.Lock] = {}

    async def execute_tool_call(
        self,
        tool_call: ToolCall,
        agent_mode: AgentMode,
        is_approved: bool = False,
    ) -> ToolResult:
        """Execute a tool call if permitted by policy and not previously executed.
        
        Guarantees single-flight execution: concurrent invocations with the same tool_call.id
        are serialized and only executed once.
        
        Args:
            tool_call: The proposed tool call to execute.
            agent_mode: Active operational mode (OBSERVE, ASSIST, AUTONOMOUS).
            is_approved: True only if explicit human approval was granted for this call.
            
        Returns:
            ToolResult with execution outcome.
            
        Raises:
            ToolNotFoundError: If tool is not registered.
            PolicyDeniedError: If policy rejects the execution outright.
            ApprovalRequiredError: If policy mandates user approval and is_approved is False.
        """
        # Acquire or initialize per-call lock
        async with self._global_lock:
            if tool_call.id not in self._in_flight_locks:
                self._in_flight_locks[tool_call.id] = asyncio.Lock()
            call_lock = self._in_flight_locks[tool_call.id]

        async with call_lock:
            # Double-checked locking: check cache after acquiring lock
            if tool_call.id in self._executed_cache:
                logger.info("tool_execution_cached", tool_call_id=tool_call.id, tool_name=tool_call.name)
                return self._executed_cache[tool_call.id]

            tool = self._registry.get(tool_call.name)
            tool_def = tool.definition

            # Evaluate against Policy Engine
            policy_req = PolicyRequest(
                agent_mode=agent_mode,
                tool_definition=tool_def,
                tool_call=tool_call,
            )
            decision = self._policy_engine.evaluate(policy_req)

            # Enforce hard DENY (even if is_approved was maliciously set, R5 / OBSERVE violations are blocked)
            if decision.decision == PolicyDecisionType.DENY:
                raise PolicyDeniedError(
                    f"Execution of '{tool_call.name}' denied by policy: {decision.reason}"
                )

            # Enforce REQUIRE_APPROVAL unless explicit human approval was granted
            if decision.decision == PolicyDecisionType.REQUIRE_APPROVAL and not is_approved:
                raise ApprovalRequiredError(
                    f"Tool '{tool_call.name}' requires user approval before execution."
                )

            # Validate arguments using tool's Pydantic schema
            try:
                tool.validate_arguments(tool_call.arguments)
            except ValidationError as val_err:
                logger.warning(
                    "tool_arguments_validation_failed",
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    error=str(val_err),
                )
                result = ToolResult(
                    tool_call_id=tool_call.id,
                    success=False,
                    error=f"Argument validation failed: {val_err}",
                )
                self._executed_cache[tool_call.id] = result
                return result

            # Execute tool
            try:
                raw_result = await tool.execute(tool_call.arguments)
                result = ToolResult(
                    tool_call_id=tool_call.id,
                    success=raw_result.success,
                    data=raw_result.data,
                    error=raw_result.error,
                )
                self._executed_cache[tool_call.id] = result
                logger.info(
                    "tool_executed_successfully",
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    success=result.success,
                )
                return result
            except Exception as exc:
                logger.error(
                    "tool_execution_exception",
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    error=str(exc),
                )
                result = ToolResult(
                    tool_call_id=tool_call.id,
                    success=False,
                    error=f"Tool execution failed: {str(exc)}",
                )
                self._executed_cache[tool_call.id] = result
                return result
