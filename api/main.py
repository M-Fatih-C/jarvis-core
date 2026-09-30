"""FastAPI application entrypoint for Jarvis Core."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from api.dependencies import get_llm_adapter
from api.routes.approvals import router as approvals_router
from api.routes.chat import router as chat_router
from api.routes.cloud import router as cloud_router
from api.routes.health import router as health_router
from api.routes.memory import router as memory_router
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
    yield
    logger.info("jarvis_shutting_down")
    llm = get_llm_adapter()
    await llm.unload()


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
