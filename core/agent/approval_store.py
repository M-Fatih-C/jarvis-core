"""In-memory store for managing human-in-the-loop approval requests."""

from datetime import datetime, timedelta, timezone
from uuid import UUID
from core.agent.exceptions import ApprovalExpiredError, JarvisError
from core.models.approval import ApprovalRequest, ApprovalStatus
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
        """Create and register a new pending approval request.
        
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

        req = ApprovalRequest(
            agent_run_id=agent_run_id,
            tool_call=tool_call,
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

    def approve(self, approval_id: UUID) -> ApprovalRequest:
        """Approve a pending request.
        
        Raises:
            JarvisError: If request not found or not in pending state.
            ApprovalExpiredError: If request has timed out.
        """
        req = self.get(approval_id)
        if req is None:
            raise JarvisError(f"Approval request '{approval_id}' not found.")

        if req.status == ApprovalStatus.EXPIRED or datetime.now(timezone.utc) > req.expires_at:
            raise ApprovalExpiredError(f"Approval request '{approval_id}' has expired.")

        if req.status != ApprovalStatus.PENDING:
            raise JarvisError(f"Approval request '{approval_id}' is already {req.status.value}.")

        updated = req.model_copy(update={"status": ApprovalStatus.APPROVED})
        self._requests[approval_id] = updated
        return updated

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
