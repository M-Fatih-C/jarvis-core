"""In-memory store for managing human-in-the-loop approval requests."""

from datetime import datetime, timedelta, timezone
from uuid import UUID
from core.agent.exceptions import ApprovalExpiredError, ApprovalIntegrityError, JarvisError
from core.models.approval import ApprovalRequest, ApprovalStatus, compute_action_digest
from core.models.tools import ToolCall


class ApprovalStore:
    """Thread-safe in-memory store for pending and resolved approval requests."""

    def __init__(self, default_ttl_seconds: int = 300) -> None:
        self._default_ttl = default_ttl_seconds
        self._requests: dict[UUID, ApprovalRequest] = {}

    def create(
        self,
        agent_run_id: UUID,
        tool_call: ToolCall,
        ttl_seconds: int | None = None,
    ) -> ApprovalRequest:
        """Create and register a new pending approval request with action digest binding.
        
        Args:
            agent_run_id: Associated AgentRun UUID.
            tool_call: Proposed ToolCall requiring approval.
            ttl_seconds: Optional lifespan in seconds.
            
        Returns:
            Newly created ApprovalRequest instance.
        """
        ttl = ttl_seconds or self._default_ttl
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)
        digest = compute_action_digest(
            agent_run_id=agent_run_id,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )

        req = ApprovalRequest(
            agent_run_id=agent_run_id,
            tool_call=tool_call,
            action_digest=digest,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
        )
        self._requests[req.id] = req
        return req

    def get(self, approval_id: UUID) -> ApprovalRequest | None:
        """Retrieve an approval request by ID, updating status if expired."""
        req = self._requests.get(approval_id)
        if req is None:
            return None

        # Check if expired
        if req.status == ApprovalStatus.PENDING and datetime.now(timezone.utc) > req.expires_at:
            expired_req = req.model_copy(update={"status": ApprovalStatus.EXPIRED})
            self._requests[approval_id] = expired_req
            return expired_req

        return req

    def approve(
        self,
        approval_id: UUID,
        current_tool_call: ToolCall | None = None,
    ) -> ApprovalRequest:
        """Approve a pending request.
        
        Args:
            approval_id: The approval request UUID.
            current_tool_call: Optional current tool call to verify action integrity before approval.

        Raises:
            JarvisError: If request not found or not in pending state.
            ApprovalExpiredError: If request has timed out.
            ApprovalIntegrityError: If action arguments or parameters have been modified.
        """
        req = self.get(approval_id)
        if req is None:
            raise JarvisError(f"Approval request '{approval_id}' not found.")

        if req.status == ApprovalStatus.EXPIRED or datetime.now(timezone.utc) > req.expires_at:
            raise ApprovalExpiredError(f"Approval request '{approval_id}' has expired.")

        if req.status != ApprovalStatus.PENDING:
            raise JarvisError(f"Approval request '{approval_id}' is already {req.status.value}.")

        if current_tool_call is not None and req.action_digest is not None:
            current_digest = compute_action_digest(
                agent_run_id=req.agent_run_id,
                tool_call_id=current_tool_call.id,
                tool_name=current_tool_call.name,
                arguments=current_tool_call.arguments,
            )
            if current_digest != req.action_digest:
                raise ApprovalIntegrityError(
                    f"Action digest mismatch on approval: expected {req.action_digest}, got {current_digest}. "
                    "Action arguments were modified before approval."
                )

        updated = req.model_copy(update={"status": ApprovalStatus.APPROVED})
        self._requests[approval_id] = updated
        return updated

    def consume(self, approval_id: UUID, tool_call: ToolCall) -> ApprovalRequest:
        """Mark an approved request as consumed upon execution, verifying integrity.
        
        Args:
            approval_id: The approval request UUID.
            tool_call: The tool call about to be executed.
            
        Raises:
            JarvisError: If not approved or not found.
            ApprovalExpiredError: If request expired.
            ApprovalIntegrityError: If already consumed or if action digest does not match.
        """
        req = self.get(approval_id)
        if req is None:
            raise JarvisError(f"Approval request '{approval_id}' not found.")

        if req.status == ApprovalStatus.EXPIRED or datetime.now(timezone.utc) > req.expires_at:
            raise ApprovalExpiredError(f"Approval request '{approval_id}' has expired.")

        if req.status != ApprovalStatus.APPROVED:
            raise JarvisError(f"Approval request '{approval_id}' cannot be consumed with status '{req.status.value}'.")

        if req.consumed_at is not None:
            raise ApprovalIntegrityError(
                f"Approval request '{approval_id}' has already been consumed at {req.consumed_at.isoformat()} (one-time use only)."
            )

        if req.action_digest is not None:
            exec_digest = compute_action_digest(
                agent_run_id=req.agent_run_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
            if exec_digest != req.action_digest:
                raise ApprovalIntegrityError(
                    f"Action digest mismatch on execution: expected {req.action_digest}, got {exec_digest}. "
                    "Arguments were altered after approval was granted."
                )

        now = datetime.now(timezone.utc)
        consumed_req = req.model_copy(update={"consumed_at": now})
        self._requests[approval_id] = consumed_req
        return consumed_req

    def reject(self, approval_id: UUID) -> ApprovalRequest:
        """Reject a pending request."""
        req = self.get(approval_id)
        if req is None:
            raise JarvisError(f"Approval request '{approval_id}' not found.")

        if req.status != ApprovalStatus.PENDING:
            raise JarvisError(f"Approval request '{approval_id}' is already {req.status.value}.")

        updated = req.model_copy(update={"status": ApprovalStatus.REJECTED})
        self._requests[approval_id] = updated
        return updated
