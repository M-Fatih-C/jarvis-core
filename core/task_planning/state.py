"""Task planning lifecycle states and deterministic transition validation for Milestone 4.2."""

from __future__ import annotations

from enum import Enum
from core.agent.exceptions import JarvisError


class TaskProposalStatus(str, Enum):
    """The 7 canonical lifecycle states for a task proposal and planned action."""

    PROPOSED = "PROPOSED"
    DISMISSED = "DISMISSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value.upper() == other.strip().upper()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value)

    @classmethod
    def from_str(cls, val: str) -> TaskProposalStatus:
        """Parse status string case-insensitively."""
        normalized = val.strip().upper()
        # Handle legacy lowercase values from Milestone 4.1
        if normalized == "COMPLETED":
            return cls.EXECUTED
        return cls(normalized)


class InvalidStateTransitionError(JarvisError):
    """Raised when an illegal lifecycle transition is attempted."""

    def __init__(self, current_status: TaskProposalStatus, target_status: TaskProposalStatus, reason: str | None = None) -> None:
        msg = f"Cannot transition task status from '{current_status.value}' to '{target_status.value}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)
        self.current_status = current_status
        self.target_status = target_status


# Deterministic transition graph
VALID_TRANSITIONS: dict[TaskProposalStatus, set[TaskProposalStatus]] = {
    TaskProposalStatus.PROPOSED: {
        TaskProposalStatus.WAITING_APPROVAL,
        TaskProposalStatus.DISMISSED,
    },
    TaskProposalStatus.WAITING_APPROVAL: {
        TaskProposalStatus.APPROVED,
        TaskProposalStatus.DISMISSED,
        TaskProposalStatus.PROPOSED,  # If user edits/replanned
    },
    TaskProposalStatus.APPROVED: {
        TaskProposalStatus.EXECUTING,
        TaskProposalStatus.DISMISSED,
    },
    TaskProposalStatus.EXECUTING: {
        TaskProposalStatus.EXECUTED,
        TaskProposalStatus.FAILED,
    },
    TaskProposalStatus.FAILED: {
        TaskProposalStatus.PROPOSED,         # Re-plan allowed after failure
        TaskProposalStatus.WAITING_APPROVAL, # Direct retry allowed
        TaskProposalStatus.DISMISSED,
    },
    TaskProposalStatus.DISMISSED: {
        TaskProposalStatus.PROPOSED,  # Can be reopened by user
    },
    TaskProposalStatus.EXECUTED: set(),  # Terminal state: no further mutations permitted
}


def transition_task_status(
    current_status: TaskProposalStatus,
    target_status: TaskProposalStatus,
    force: bool = False,
) -> TaskProposalStatus:
    """Validate and execute a deterministic state transition.

    Args:
        current_status: Current lifecycle state.
        target_status: Desired next lifecycle state.
        force: If True, bypass validation (internal recovery only).

    Returns:
        The target TaskProposalStatus.

    Raises:
        InvalidStateTransitionError: If the transition is not in the allowed graph.
    """
    if current_status == target_status:
        return current_status

    if force:
        return target_status

    allowed = VALID_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise InvalidStateTransitionError(
            current_status=current_status,
            target_status=target_status,
            reason=f"Allowed next states from '{current_status.value}' are: {[s.value for s in allowed]}",
        )

    return target_status
