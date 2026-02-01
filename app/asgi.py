"""ASGI entry point for uvicorn with hot reload support.

Usage:
    uvicorn app.asgi:app --reload --host 0.0.0.0 --port 8742
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import AgentConfig
from app.logging_filters import install_uvicorn_access_log_filters
from app.main import Application
from app.routers import create_task_router, create_webhook_router
from app.security.whitelist import live_allowed_users
from app.observability.health_state import snapshot as health_snapshot

# Global application instance for lifespan management
_application: Application | None = None


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan for ASGI server."""
    global _application

    config = AgentConfig.from_json_file()
    install_uvicorn_access_log_filters()
    _application = Application(config)
    await _application.setup()

    # Register routers
    if _application.task_service:
        task_router = create_task_router(
            _application.task_service,
            allowed_users=live_allowed_users(config.secrets_path),
        )
        fastapi_app.include_router(task_router)

    if _application.webhook_service:
        webhook_router = create_webhook_router(
            _application.webhook_service,
            allowed_users=live_allowed_users(config.secrets_path),
        )
        fastapi_app.include_router(webhook_router)

    # Start background services
    await _application.start_background_services()

    yield

    # Shutdown
    await _application.shutdown()
    _application = None


# Create FastAPI app with lifespan
app = FastAPI(
    title="Mordecai",
    description="Mordecai — multi-user AI agent platform with Telegram",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    # Note: config is loaded in lifespan; fall back to default if something goes wrong.
    try:
        cfg = AgentConfig.from_json_file()
        stall_seconds = int(getattr(cfg, "health_stall_seconds", 180) or 180)
        fail_on_stall = bool(getattr(cfg, "health_fail_on_stall", True))
    except Exception:
        stall_seconds = 180
        fail_on_stall = True

    snap = health_snapshot(stall_seconds=stall_seconds)
    if snap.status != "healthy" and fail_on_stall:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=snap.to_dict(mode="json"))
    return snap.to_dict(mode="json")
