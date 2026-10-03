"""FastAPI application entrypoint for Jarvis Core."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from api.dependencies import get_llm_adapter, get_agent_runtime
from api.routes.approvals import router as approvals_router
from api.routes.chat import router as chat_router
from api.routes.cloud import router as cloud_router
from api.routes.health import router as health_router
from api.routes.memory import router as memory_router
from api.routes.task_planning import router as task_planning_router
from core.agent.exceptions import JarvisError
from core.config.settings import get_settings
from core.logging.setup import get_logger, setup_logging

logger = get_logger("jarvis.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan managing startup and shutdown tasks."""
    setup_logging()
    settings = get_settings()
    logger.info(
        "jarvis_starting",
        app_name=settings.app_name,
        environment=settings.environment,
        host=settings.host,
        port=settings.port,
        model_id=settings.model_id,
    )
    worker = device_service = client_provider = scheduler = None
    if settings.environment == "production":
        if settings.apple_integration_provider != "native_macos":
            raise RuntimeError("Production requires native EventKit tools")
        if settings.embedding_provider == "mock":
            raise RuntimeError("Production requires local semantic embeddings")
        await get_llm_adapter().load()
    if settings.firebase_enabled:
        if settings.jarvis_uid == "default_user" or settings.firebase_project_id == "jarvis-local-dev":
            raise RuntimeError("Configure the production Firebase project and owner UID")
        if settings.environment == "production" and settings.firestore_emulator_host:
            raise RuntimeError("Production cannot use the Firestore emulator")
        from core.cloud.command_worker import CommandWorker
        from core.cloud.device_service import DeviceService
        from integrations.firebase.client import FirestoreClientProvider
        from integrations.firebase.command_repository import FirestoreCommandRepository
        from integrations.firebase.device_repository import FirestoreDeviceRepository
        client_provider = FirestoreClientProvider(settings)
        from core.cloud.client_requests import ClientRequests
        from api.dependencies import get_email_storage, get_task_planner, get_calendar_manager, get_task_approval_service, get_memory_service
        screens = ClientRequests(owner=settings.jarvis_uid, storage=get_email_storage(), planner=get_task_planner(),
            calendar_manager=get_calendar_manager(), approval_service=get_task_approval_service(),
            memory=get_memory_service(), mode=settings.default_agent_mode)
        worker = CommandWorker(FirestoreCommandRepository(client_provider, settings=settings), get_agent_runtime(),
            settings=settings, client_requests=screens)
        async def device_health():
            from api.dependencies import get_mac_bridge_client
            from integrations.macos.health import check_mac_agent_health
            native = await check_mac_agent_health(get_mac_bridge_client())
            from integrations.gmail.auth import KeychainTokenStore
            from core.build_manager.manager import BuildManager
            gmail_configured = False
            try:
                gmail_configured = KeychainTokenStore().load_tokens("default") is not None
            except Exception:
                pass
            build = BuildManager().state
            monitoring = await get_email_storage().get_monitoring_start()
            return {"model_ready": await get_llm_adapter().health(), "model": settings.model_id,
                    "mac_agent_connected": native.get("connected", False), "worker_running": worker._running,
                    "calendar_permission": native.get("calendar_permission", "unknown"),
                    "reminders_permission": native.get("reminders_permission", "unknown"),
                    "gmail_authorized": gmail_configured, "gmail_new_only": monitoring is not None,
                    "gmail_schedule_enabled": bool(scheduler and scheduler._running and scheduler.enabled),
                    "gmail_last_success": scheduler.last_run.isoformat() if scheduler and scheduler.last_run else None,
                    "signature_auto_renew": build.auto_renew_enabled,
                    "signature_expires": build.last_provisioning_info.expiration_date.isoformat()
                        if build.last_provisioning_info and build.last_provisioning_info.expiration_date else None}
        device_service = DeviceService(FirestoreDeviceRepository(client_provider, settings=settings), settings=settings, health_provider=device_health)
        await worker.start()
        await device_service.start()
    if settings.gmail_enabled and settings.email_sync_schedule_enabled:
        from api.dependencies import get_gmail_pipeline
        from integrations.gmail.scheduler import EmailSyncScheduler
        scheduler = EmailSyncScheduler(get_gmail_pipeline(), settings=settings)
        scheduler.start()
    app.state.command_worker = worker
    try:
        yield
    finally:
        if scheduler:
            scheduler.stop()
        if worker:
            await worker.stop()
        if device_service:
            await device_service.stop()
        if client_provider:
            await client_provider.close()
        logger.info("jarvis_shutting_down")
        await get_llm_adapter().unload()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Jarvis V1 Local-First AI Agent Core for macOS",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register API routers
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(approvals_router)
    app.include_router(memory_router)
    app.include_router(cloud_router)
    app.include_router(task_planning_router)

    # Centralized exception handlers ensuring safe client responses
    @app.exception_handler(JarvisError)
    async def jarvis_error_handler(request: Request, exc: JarvisError) -> JSONResponse:
        logger.warning(
            "jarvis_handled_error",
            path=request.url.path,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": type(exc).__name__,
                "message": exc.message,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_system_error",
            path=request.url.path,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "InternalServerError",
                "message": "An unexpected error occurred while processing the request.",
            },
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    # Explicitly enforce local loopback binding
    uvicorn.run(
        "api.main:app",
        host=cfg.host,
        port=cfg.port,
        reload=cfg.environment == "development",
    )
