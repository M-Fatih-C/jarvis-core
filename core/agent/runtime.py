"""Core Agent Runtime executing the cognitive loop and coordinating components."""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.exceptions import (
    AgentStateError,
    AgentStepLimitError,
    ApprovalExpiredError,
    ApprovalRequiredError,
    JarvisError,
    PolicyDeniedError,
    ToolCallParseError,
)
from core.agent.state_machine import AgentState, AgentStateMachine
from core.config.settings import Settings, get_settings
from core.llm.base import LLMAdapter
from core.logging.setup import get_logger
from core.models.agent import AgentMode, AgentRun
from core.models.approval import ApprovalRequest, ApprovalStatus
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall, ToolResult
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType, PolicyRequest
from core.tools.executor import ToolExecutor
from core.tools.registry import ToolRegistry

logger = get_logger("jarvis.runtime")


class AgentRuntime:
    """Orchestrates agent execution from user prompt to completion or approval."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_registry: ToolRegistry,
        policy_engine: PolicyEngine,
        tool_executor: ToolExecutor,
        approval_store: ApprovalStore | None = None,
        context_builder: ContextBuilder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._llm = llm_adapter
        self._tools = tool_registry
        self._policy = policy_engine
        self._executor = tool_executor
        self._approvals = approval_store or ApprovalStore()
        self._context_builder = context_builder or ContextBuilder()
        self._settings = settings or get_settings()

        # In-memory storage for active runs and conversation histories
        self._runs: dict[UUID, AgentRun] = {}
        self._contexts: dict[UUID, list[ChatMessage]] = {}
        self._state_machines: dict[UUID, AgentStateMachine] = {}

    def get_run(self, run_id: UUID) -> AgentRun | None:
        """Fetch an AgentRun by its ID."""
        return self._runs.get(run_id)

    @property
    def approval_store(self) -> ApprovalStore:
        """Accessor for approval store."""
        return self._approvals

    async def run(
        self,
        user_input: str,
        source: str = "chat",
        agent_mode: AgentMode | None = None,
        anchor_datetime: datetime | None = None,
    ) -> AgentRun:
        """Main entry point to initiate and execute an agent run.
        
        Args:
            user_input: Message or request from user.
            source: Channel source (e.g. 'chat').
            agent_mode: Override agent operating mode (defaulting to config).
            anchor_datetime: Optional explicit datetime anchor for testing.
            
        Returns:
            The resulting AgentRun in COMPLETED, WAITING_APPROVAL, or FAILED state.
        """
        mode = agent_mode or AgentMode(self._settings.default_agent_mode)
        now = datetime.now(timezone.utc)
        run_id = uuid4()

        sm = AgentStateMachine(AgentState.CREATED)
        self._state_machines[run_id] = sm

        agent_run = AgentRun(
            id=run_id,
            state=sm.current_state,
            source=source,
            agent_mode=mode,
            user_input=user_input,
            step_count=0,
            tool_call_count=0,
            created_at=now,
            updated_at=now,
        )
        self._runs[run_id] = agent_run

        logger.info(
            "agent_run_started",
            run_id=str(run_id),
            mode=mode.value,
            source=source,
        )

        try:
            # 1. CONTEXT_BUILDING with temporal anchors
            self._transition(run_id, AgentState.CONTEXT_BUILDING)
            messages = self._context_builder.prepare_initial_messages(
                user_input,
                mode,
                anchor_datetime=anchor_datetime,
            )
            self._contexts[run_id] = messages

            # 2. Start cognitive loop
            return await self._step_loop(run_id)

        except Exception as exc:
            logger.error("agent_run_exception", run_id=str(run_id), error=str(exc))
            self._transition_failed(run_id, str(exc))
            return self._runs[run_id]

    async def _step_loop(self, run_id: UUID) -> AgentRun:
        """Execute iterative cognitive steps up to configured limits."""
        agent_run = self._runs[run_id]
        messages = self._contexts[run_id]
        while agent_run.step_count < self._settings.max_agent_steps:
            agent_run.step_count += 1
            agent_run.updated_at = datetime.now(timezone.utc)

            # Transition to THINKING
            self._transition(run_id, AgentState.THINKING)

            tool_defs = self._tools.list_definitions()
            step_parse_retries = 0

            # Generate with LLM, supporting parse error repair loop up to max_retries
            while True:
                try:
                    llm_response = await self._llm.generate_with_tools(messages, tool_defs)
                    break
                except ToolCallParseError as parse_err:
                    step_parse_retries += 1
                    if step_parse_retries > self._settings.max_retries:
                        logger.error(
                            "tool_call_parse_failed_max_retries",
                            run_id=str(run_id),
                            retries=step_parse_retries,
                            error=str(parse_err),
                        )
                        raise parse_err
                    logger.warning(
                        "tool_call_parse_failed_retrying",
                        run_id=str(run_id),
                        retries=step_parse_retries,
                        error=str(parse_err),
                    )
                    messages.append(ChatMessage(
                        role=MessageRole.USER,
                        content=(
                            f"System notice: Your tool call could not be parsed: {str(parse_err)}. "
                            "Please re-issue your tool call formatted strictly with required properties."
                        ),
                        is_internal=True,
                    ))

            # If no tools called, we proceed to final response
            if not llm_response.tool_calls:
                self._transition(run_id, AgentState.RESPONDING)
                agent_run.final_response = llm_response.content or ""
                # Append final assistant message to conversation history
                messages.append(ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=agent_run.final_response,
                ))
                self._transition(run_id, AgentState.COMPLETED)
                logger.info(
                    "agent_run_completed_no_tools",
                    run_id=str(run_id),
                    steps=agent_run.step_count,
                )
                return agent_run

            # Process proposed tool
            self._transition(run_id, AgentState.TOOL_PROPOSED)
            # Enforce max 1 tool per step
            tool_call = llm_response.tool_calls[0]

            # Append assistant message with proposed tool_calls to conversation history
            messages.append(ChatMessage(
                role=MessageRole.ASSISTANT,
                content=llm_response.content or "",
                tool_calls=[tool_call],
            ))

            # Check tool call limit
            if agent_run.tool_call_count >= self._settings.max_tool_calls:
                raise AgentStepLimitError(
                    f"Exceeded maximum allowed tool calls ({self._settings.max_tool_calls})."
                )

            # Transition to POLICY_CHECK
            self._transition(run_id, AgentState.POLICY_CHECK)
            tool = self._tools.get(tool_call.name)
            policy_req = PolicyRequest(
                agent_mode=agent_run.agent_mode,
                tool_definition=tool.definition,
                tool_call=tool_call,
            )
            decision = self._policy.evaluate(policy_req)

            if decision.decision == PolicyDecisionType.DENY:
                logger.warning(
                    "tool_denied_by_policy",
                    run_id=str(run_id),
                    tool=tool_call.name,
                    reason=decision.reason,
                )
                self._transition(run_id, AgentState.PROCESSING_TOOL_RESULT)
                messages.append(ChatMessage(
                    role=MessageRole.TOOL,
                    content=f"Error: Policy Engine denied action '{tool_call.name}': {decision.reason}",
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                ))
                # Continue loop to allow LLM to formulate explanation
                continue

            elif decision.decision == PolicyDecisionType.REQUIRE_APPROVAL:
                # Transition to WAITING_APPROVAL
                self._transition(run_id, AgentState.WAITING_APPROVAL)
                approval_req = self._approvals.create(
                    agent_run_id=run_id,
                    tool_call=tool_call,
                    ttl_seconds=self._settings.approval_ttl_seconds,
                )
                agent_run.pending_approval_id = approval_req.id
                agent_run.pending_tool_call = tool_call

                if llm_response.content:
                    agent_run.final_response = llm_response.content
                else:
                    agent_run.final_response = (
                        f"Bu işlem onayınızı gerektiriyor: {tool_call.name} "
                        f"ile parametreler: {json.dumps(tool_call.arguments, ensure_ascii=False)}"
                    )

                logger.info(
                    "agent_run_waiting_approval",
                    run_id=str(run_id),
                    approval_id=str(approval_req.id),
                    tool=tool_call.name,
                )
                return agent_run

            elif decision.decision == PolicyDecisionType.ALLOW:
                # Execute tool directly
                self._transition(run_id, AgentState.EXECUTING_TOOL)
                tool_result = await self._executor.execute_tool_call(
                    tool_call,
                    agent_mode=agent_run.agent_mode,
                    is_approved=False,
                )
                agent_run.tool_call_count += 1

                self._transition(run_id, AgentState.PROCESSING_TOOL_RESULT)
                result_content = (
                    json.dumps(tool_result.data, ensure_ascii=False)
                    if tool_result.success
                    else f"Tool execution failed: {tool_result.error}"
                )
                messages.append(ChatMessage(
                    role=MessageRole.TOOL,
                    content=result_content,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                ))
                # Continue iterative loop with tool result in context

        raise AgentStepLimitError(
            f"Exceeded maximum reasoning steps ({self._settings.max_agent_steps})."
        )

    async def resume_approval(self, approval_id: UUID) -> AgentRun:
        """Resume an AgentRun paused in WAITING_APPROVAL, continuing the cognitive loop."""
        approval_req = self._approvals.approve(approval_id)
        run_id = approval_req.agent_run_id
        agent_run = self._runs[run_id]

        if agent_run.state != AgentState.WAITING_APPROVAL:
            raise AgentStateError(
                f"Agent run is in {agent_run.state.value}, expected {AgentState.WAITING_APPROVAL.value}"
            )

        tool_call = approval_req.tool_call
        messages = self._contexts[run_id]

        # Transition WAITING_APPROVAL -> EXECUTING_TOOL
        self._transition(run_id, AgentState.EXECUTING_TOOL)

        # Execute with verified approval
        tool_result = await self._executor.execute_tool_call(
            tool_call,
            agent_mode=agent_run.agent_mode,
            is_approved=True,
        )
        agent_run.tool_call_count += 1
        agent_run.pending_approval_id = None
        agent_run.pending_tool_call = None

        # Transition EXECUTING_TOOL -> PROCESSING_TOOL_RESULT
        self._transition(run_id, AgentState.PROCESSING_TOOL_RESULT)
        result_content = (
            json.dumps(tool_result.data, ensure_ascii=False)
            if tool_result.success
            else f"Tool execution error: {tool_result.error}"
        )
        messages.append(ChatMessage(
            role=MessageRole.TOOL,
            content=result_content,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
        ))

        # Re-enter the multi-step cognitive loop seamlessly
        return await self._step_loop(run_id)

    async def resume_rejection(self, approval_id: UUID, reason: str | None = None) -> AgentRun:
        """Handle user rejection of a proposed tool call."""
        approval_req = self._approvals.reject(approval_id)
        run_id = approval_req.agent_run_id
        agent_run = self._runs[run_id]

        if agent_run.state != AgentState.WAITING_APPROVAL:
            raise AgentStateError(
                f"Agent run is in {agent_run.state.value}, expected {AgentState.WAITING_APPROVAL.value}"
            )

        tool_call = approval_req.tool_call
        messages = self._contexts[run_id]

        agent_run.pending_approval_id = None
        agent_run.pending_tool_call = None

        # Transition WAITING_APPROVAL -> PROCESSING_TOOL_RESULT -> RESPONDING -> COMPLETED
        self._transition(run_id, AgentState.PROCESSING_TOOL_RESULT)
        rejection_reason = reason or "İşlem kullanıcı tarafından reddedildi."

        messages.append(ChatMessage(
            role=MessageRole.TOOL,
            content=f"Tool '{tool_call.name}' was rejected by user: {rejection_reason}",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
        ))

        self._transition(run_id, AgentState.RESPONDING)
        agent_run.final_response = rejection_reason
        messages.append(ChatMessage(
            role=MessageRole.ASSISTANT,
            content=rejection_reason,
        ))
        self._transition(run_id, AgentState.COMPLETED)

        logger.info("agent_run_completed_after_rejection", run_id=str(run_id))
        return agent_run

    def _transition(self, run_id: UUID, next_state: AgentState) -> None:
        """Perform verified state transition on both state machine and run model."""
        sm = self._state_machines[run_id]
        sm.transition_to(next_state)
        run = self._runs[run_id]
        run.state = next_state
        run.updated_at = datetime.now(timezone.utc)
        logger.info("agent_state_transition", run_id=str(run_id), new_state=next_state.value)

    def _transition_failed(self, run_id: UUID, error_msg: str) -> None:
        """Mark agent run as failed."""
        if run_id in self._state_machines:
            sm = self._state_machines[run_id]
            try:
                sm.transition_to(AgentState.FAILED)
            except Exception:
                pass
        if run_id in self._runs:
            run = self._runs[run_id]
            run.state = AgentState.FAILED
            run.error = error_msg
            run.updated_at = datetime.now(timezone.utc)
