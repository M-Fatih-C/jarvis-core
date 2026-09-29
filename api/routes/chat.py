"""Chat route handling user prompt submissions and runs."""

from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from api.dependencies import get_agent_runtime
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.models.agent import AgentMode

router = APIRouter(prefix="/v1", tags=["Chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="User prompt or instruction")
    agent_mode: AgentMode | None = Field(default=None, description="Optional override for operational mode")


class ApprovalPayload(BaseModel):
    id: UUID
    tool: str
    arguments: dict[str, Any]


class ChatResponse(BaseModel):
    runId: UUID
    status: str
    message: str
    approval: ApprovalPayload | None = None


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat_endpoint(
    req: ChatRequest,
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> ChatResponse:
    """Submit a message to the Jarvis agent runtime and process actions."""
    run = await runtime.run(user_input=req.message, agent_mode=req.agent_mode)

    if run.state == AgentState.FAILED:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent run encountered an internal failure.",
        )

    approval_payload: ApprovalPayload | None = None
    if run.state == AgentState.WAITING_APPROVAL and run.pending_approval_id and run.pending_tool_call:
        approval_payload = ApprovalPayload(
            id=run.pending_approval_id,
            tool=run.pending_tool_call.name,
            arguments=run.pending_tool_call.arguments,
        )

    return ChatResponse(
        runId=run.id,
        status=run.state.value,
        message=run.final_response or "",
        approval=approval_payload,
    )
