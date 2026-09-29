"""Approval routes allowing users to grant or reject tool execution requests."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from api.dependencies import get_agent_runtime
from core.agent.exceptions import ApprovalExpiredError, JarvisError
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState

router = APIRouter(prefix="/v1/approvals", tags=["Approvals"])


class ApprovalActionResponse(BaseModel):
    runId: UUID
    status: str
    message: str


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, description="Optional explanation for rejecting action")


@router.post("/{approval_id}/approve", response_model=ApprovalActionResponse)
async def approve_tool(
    approval_id: UUID,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> ApprovalActionResponse:
    """Approve a pending tool action, resuming the agent run to execution and completion."""
    try:
        run = await runtime.resume_approval(approval_id)
    except ApprovalExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Approval request has expired.",
        ) from exc
    except JarvisError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return ApprovalActionResponse(
        runId=run.id,
        status=run.state.value,
        message=run.final_response or "",
    )


@router.post("/{approval_id}/reject", response_model=ApprovalActionResponse)
async def reject_tool(
    approval_id: UUID,
    body: RejectRequest | None = None,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> ApprovalActionResponse:
    """Reject a pending tool action, terminating the tool step and notifying the agent run."""
    reason = body.reason if body else None
    try:
        run = await runtime.resume_rejection(approval_id, reason=reason)
    except JarvisError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return ApprovalActionResponse(
        runId=run.id,
        status=run.state.value,
        message=run.final_response or "İşlem reddedildi.",
    )
