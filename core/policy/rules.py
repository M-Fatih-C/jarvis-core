"""Policy rules matrix governing tool execution by AgentMode and RiskLevel."""

from core.models.agent import AgentMode
from core.models.tools import RiskLevel
from core.policy.risk import PolicyDecisionType

# Default base decision matrix mapped to (AgentMode, RiskLevel)
DEFAULT_POLICY_MATRIX: dict[tuple[AgentMode, RiskLevel], PolicyDecisionType] = {
    # ASSIST mode (Default)
    (AgentMode.ASSIST, RiskLevel.R0_READ): PolicyDecisionType.ALLOW,
    (AgentMode.ASSIST, RiskLevel.R1_LOCAL_LOW): PolicyDecisionType.ALLOW,
    (AgentMode.ASSIST, RiskLevel.R2_WRITE): PolicyDecisionType.REQUIRE_APPROVAL,
    (AgentMode.ASSIST, RiskLevel.R3_EXTERNAL): PolicyDecisionType.REQUIRE_APPROVAL,
    (AgentMode.ASSIST, RiskLevel.R4_DESTRUCTIVE): PolicyDecisionType.REQUIRE_APPROVAL,
    (AgentMode.ASSIST, RiskLevel.R5_SENSITIVE): PolicyDecisionType.DENY,

    # OBSERVE mode (Strict read-only)
    (AgentMode.OBSERVE, RiskLevel.R0_READ): PolicyDecisionType.ALLOW,
    (AgentMode.OBSERVE, RiskLevel.R1_LOCAL_LOW): PolicyDecisionType.DENY,
    (AgentMode.OBSERVE, RiskLevel.R2_WRITE): PolicyDecisionType.DENY,
    (AgentMode.OBSERVE, RiskLevel.R3_EXTERNAL): PolicyDecisionType.DENY,
    (AgentMode.OBSERVE, RiskLevel.R4_DESTRUCTIVE): PolicyDecisionType.DENY,
    (AgentMode.OBSERVE, RiskLevel.R5_SENSITIVE): PolicyDecisionType.DENY,

    # AUTONOMOUS mode (V1: R0, R1 allowed; R2 configurable allowed; R3, R4 approval; R5 deny)
    (AgentMode.AUTONOMOUS, RiskLevel.R0_READ): PolicyDecisionType.ALLOW,
    (AgentMode.AUTONOMOUS, RiskLevel.R1_LOCAL_LOW): PolicyDecisionType.ALLOW,
    (AgentMode.AUTONOMOUS, RiskLevel.R2_WRITE): PolicyDecisionType.ALLOW,
    (AgentMode.AUTONOMOUS, RiskLevel.R3_EXTERNAL): PolicyDecisionType.REQUIRE_APPROVAL,
    (AgentMode.AUTONOMOUS, RiskLevel.R4_DESTRUCTIVE): PolicyDecisionType.REQUIRE_APPROVAL,
    (AgentMode.AUTONOMOUS, RiskLevel.R5_SENSITIVE): PolicyDecisionType.DENY,
}
