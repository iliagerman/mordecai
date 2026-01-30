"""ASGI entry point for uvicorn with hot reload support.

Usage:
    uvicorn app.asgi:app --reload --host 0.0.0.0 --port 8742
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import AgentConfig
from app.main import Application
from app.routers import create_task_router, create_webhook_router

# Global application instance for lifespan management
_application: Application | None = None


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan for ASGI server."""
    global _application

    config = AgentConfig.from_json_file()
    _application = Application(config)
    await _application.setup()

    # Register routers
    if _application.task_service:
        task_router = create_task_router(_application.task_service)
        fastapi_app.include_router(task_router)

    if _application.webhook_service:
        webhook_router = create_webhook_router(_application.webhook_service)
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
    return {"status": "healthy", "service": "mordecai"}
