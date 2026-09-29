"""Unit tests for the Agent State Machine and lifecycle enforcement."""

import pytest
from core.agent.exceptions import AgentStateError
from core.agent.state_machine import AgentState, AgentStateMachine


def test_created_to_thinking_directly_forbidden() -> None:
    """CREATED → THINKING directly forbidden."""
    sm = AgentStateMachine(AgentState.CREATED)
    with pytest.raises(AgentStateError, match="cannot transition from created to thinking"):
        sm.transition_to(AgentState.THINKING)


def test_created_to_context_building_allowed() -> None:
    """CREATED → CONTEXT_BUILDING allowed."""
    sm = AgentStateMachine(AgentState.CREATED)
    sm.transition_to(AgentState.CONTEXT_BUILDING)
    assert sm.current_state == AgentState.CONTEXT_BUILDING


def test_waiting_approval_to_executing_tool_allowed() -> None:
    """WAITING_APPROVAL → EXECUTING_TOOL allowed after approval."""
    sm = AgentStateMachine(AgentState.WAITING_APPROVAL)
    sm.transition_to(AgentState.EXECUTING_TOOL)
    assert sm.current_state == AgentState.EXECUTING_TOOL


def test_full_successful_flow_with_tools() -> None:
    """Test standard complete path through state machine."""
    sm = AgentStateMachine()
    assert sm.current_state == AgentState.CREATED

    sm.transition_to(AgentState.CONTEXT_BUILDING)
    sm.transition_to(AgentState.THINKING)
    sm.transition_to(AgentState.TOOL_PROPOSED)
    sm.transition_to(AgentState.POLICY_CHECK)
    sm.transition_to(AgentState.EXECUTING_TOOL)
    sm.transition_to(AgentState.PROCESSING_TOOL_RESULT)
    sm.transition_to(AgentState.THINKING)
    sm.transition_to(AgentState.RESPONDING)
    sm.transition_to(AgentState.COMPLETED)

    assert sm.is_terminal
    assert sm.current_state == AgentState.COMPLETED


def test_terminal_state_cannot_transition() -> None:
    """COMPLETED state allows no further transitions."""
    sm = AgentStateMachine(AgentState.COMPLETED)
    with pytest.raises(AgentStateError):
        sm.transition_to(AgentState.THINKING)
