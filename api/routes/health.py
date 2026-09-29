"""Health check endpoint for Jarvis."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.dependencies import get_llm_adapter
from core.llm.base import LLMAdapter

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    model: str
    agent: str


@router.get("/health", response_model=HealthResponse)
async def health_check(llm: LLMAdapter = Depends(get_llm_adapter)) -> HealthResponse:
    """Check system readiness and whether model weights are resident in memory."""
    is_loaded = await llm.health()
    return HealthResponse(
        status="ok",
        model="loaded" if is_loaded else "not_loaded",
        agent="ready",
    )
