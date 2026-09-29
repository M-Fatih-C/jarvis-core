"""Deterministic Policy Engine enforcing safety boundaries on proposed tool calls."""

from core.logging.setup import get_logger
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall, ToolDefinition
from core.policy.risk import PolicyDecision, PolicyDecisionType, PolicyRequest
from core.policy.rules import DEFAULT_POLICY_MATRIX

logger = get_logger("jarvis.policy")


class PolicyEngine:
    """Evaluates whether proposed tool actions are allowed, require approval, or are denied."""

    def __init__(self, matrix: dict[tuple[AgentMode, RiskLevel], PolicyDecisionType] | None = None) -> None:
        self._matrix = matrix or DEFAULT_POLICY_MATRIX

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate a tool execution request against active policy rules.
        
        This evaluation is strictly isolated from LLM output interpretation.
        
        Args:
            request: The policy evaluation request containing mode, definition, and call.
            
        Returns:
            PolicyDecision with outcome (ALLOW, REQUIRE_APPROVAL, DENY) and explanatory rationale.
        """
        tool_def = request.tool_definition
        mode = request.agent_mode
        risk = tool_def.risk_level

        # Rule 1: R5 is strictly denied under all circumstances
        if risk == RiskLevel.R5_SENSITIVE:
            decision = PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reason=f"Tool '{tool_def.name}' is classified as R5_SENSITIVE and cannot be executed.",
            )
            self._log_decision(request, decision)
            return decision

        # Rule 2: In OBSERVE mode, all non-read actions are strictly denied
        if mode == AgentMode.OBSERVE and risk != RiskLevel.R0_READ:
            decision = PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reason=f"Agent is operating in OBSERVE mode; tool '{tool_def.name}' (Risk: {risk.name}) is denied.",
            )
            self._log_decision(request, decision)
            return decision

        # Rule 3: Explicit tool-level approval requirement override
        if tool_def.requires_approval:
            decision = PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_APPROVAL,
                reason=f"Tool '{tool_def.name}' definition explicitly requires human approval.",
            )
            self._log_decision(request, decision)
            return decision

        # Rule 4: Matrix lookup by (AgentMode, RiskLevel)
        decision_type = self._matrix.get((mode, risk), PolicyDecisionType.DENY)
        reason = f"Evaluated under mode={mode.value}, risk={risk.name}: decision={decision_type.value}"

        decision = PolicyDecision(decision=decision_type, reason=reason)
        self._log_decision(request, decision)
        return decision

    def _log_decision(self, request: PolicyRequest, decision: PolicyDecision) -> None:
        logger.info(
            "policy_evaluated",
            tool_name=request.tool_definition.name,
            tool_call_id=request.tool_call.id,
            agent_mode=request.agent_mode.value,
            risk_level=request.tool_definition.risk_level.name,
            decision=decision.decision.value,
            reason=decision.reason,
        )
