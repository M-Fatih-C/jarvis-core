"""FastAPI routes for cloud sync and device telemetry."""

from typing import Any
from fastapi import APIRouter, Depends
from api.dependencies import get_agent_runtime
from core.agent.runtime import AgentRuntime
from core.config.settings import get_settings

router = APIRouter(prefix="/v1", tags=["cloud"])


@router.get("/cloud/status")
async def get_cloud_status(
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> dict[str, Any]:
    """Retrieve operational status of the cloud sync and device system."""
    settings = get_settings()
    return {
        "cloud": {
            "enabled": settings.firebase_enabled,
            "project_id": settings.firebase_project_id,
            "emulator": settings.firestore_emulator_host or "disabled",
            "device": {
                "id": settings.device_id,
                "name": settings.device_name,
                "type": settings.device_type,
            },
        }
    }
