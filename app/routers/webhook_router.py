"""Webhook API endpoints.

Routers handle HTTP concerns only - no business logic.
All business logic is delegated to WebhookService.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from app.enums import WebhookEventType
from app.models.base import JsonModel
from app.security.whitelist import enforce_whitelist_or_403

if TYPE_CHECKING:
    from app.services.webhook_service import WebhookService


class WebhookEvent(JsonModel):
    """Webhook event model."""

    event_type: WebhookEventType
    payload: dict
    timestamp: datetime
    source: str | None = None


class WebhookResponse(JsonModel):
    """Webhook response model."""

    status: str
    result: dict | None = None
    error: str | None = None


def create_webhook_router(
    webhook_service: "WebhookService",
    *,
    allowed_users: list[str] | None = None,
) -> APIRouter:
    """Create webhook router with injected service.

    Args:
        webhook_service: WebhookService instance for business logic

    Returns:
        APIRouter with webhook endpoints configured
    """
    router = APIRouter(prefix="/webhook", tags=["webhooks"])

    @router.post("", response_model=WebhookResponse)
    async def handle_webhook(request: Request) -> WebhookResponse:
        """Handle incoming webhook.

        Args:
            request: FastAPI Request object

        Returns:
            WebhookResponse with status and result

        Raises:
            HTTPException: 400 for bad request, 500 for server errors
        """
        try:
            body = await request.json()
            event = WebhookEvent.model_validate(body)

            # If webhook payload is user-scoped, enforce whitelist.
            # Accept both snake_case and camelCase keys.
            user_id = None
            if isinstance(event.payload, dict):
                user_id = event.payload.get("user_id") or event.payload.get("userId")
            if isinstance(user_id, str) and user_id.strip():
                enforce_whitelist_or_403(user_id, allowed_users)

            result = await webhook_service.process_event(event)
            return WebhookResponse(status="success", result=result)
        except HTTPException:
            # Preserve FastAPI's intended status codes (e.g., 403 from whitelist enforcement)
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router
