"""Agent state machine governing valid lifecycle transitions."""

from enum import Enum
from core.agent.exceptions import AgentStateError


class AgentState(str, Enum):
    """Explicit lifecycle states for an AgentRun."""
    CREATED = "created"
    CONTEXT_BUILDING = "context_building"
    THINKING = "thinking"
    TOOL_PROPOSED = "tool_proposed"
    POLICY_CHECK = "policy_check"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING_TOOL = "executing_tool"
    PROCESSING_TOOL_RESULT = "processing_tool_result"
    RESPONDING = "responding"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Set of allowed state transitions
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.CREATED: {
        AgentState.CONTEXT_BUILDING,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.CONTEXT_BUILDING: {
        AgentState.THINKING,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.THINKING: {
        AgentState.TOOL_PROPOSED,
        AgentState.RESPONDING,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.TOOL_PROPOSED: {
        AgentState.POLICY_CHECK,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.POLICY_CHECK: {
        AgentState.WAITING_APPROVAL,
        AgentState.EXECUTING_TOOL,
        AgentState.PROCESSING_TOOL_RESULT,  # When policy denies or warns
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.WAITING_APPROVAL: {
        AgentState.EXECUTING_TOOL,          # Approved
        AgentState.PROCESSING_TOOL_RESULT,  # Rejected or Expired
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.EXECUTING_TOOL: {
        AgentState.PROCESSING_TOOL_RESULT,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.PROCESSING_TOOL_RESULT: {
        AgentState.THINKING,
        AgentState.RESPONDING,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.RESPONDING: {
        AgentState.COMPLETED,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.COMPLETED: set(),
    AgentState.FAILED: set(),
    AgentState.CANCELLED: set(),
}


class AgentStateMachine:
    """Manages and enforces valid state transitions for an agent run."""

    def __init__(self, initial_state: AgentState = AgentState.CREATED) -> None:
        self._current_state = initial_state

    @property
    def current_state(self) -> AgentState:
        """Get the current agent state."""
        return self._current_state

    @property
    def is_terminal(self) -> bool:
        """Check if the state machine is in a terminal state."""
        return self._current_state in {
            AgentState.COMPLETED,
            AgentState.FAILED,
            AgentState.CANCELLED,
        }

    def transition_to(self, next_state: AgentState) -> None:
        """Transition to a new state or raise AgentStateError if invalid.
        
        Args:
            next_state: The desired target AgentState.
            
        Raises:
            AgentStateError: If the transition from current_state to next_state is disallowed.
        """
        allowed = VALID_TRANSITIONS.get(self._current_state, set())
        if next_state not in allowed:
            raise AgentStateError(
                f"Invalid state transition: cannot transition from {self._current_state.value} to {next_state.value}"
            )
        self._current_state = next_state
