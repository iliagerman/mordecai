"""Webhook processing service.

This service handles all webhook-related business logic, processing
incoming webhook events and routing them to appropriate handlers.

Requirements:
- 7.2: Pass webhook events to the Agent for processing
- 7.3: Process webhook events and take appropriate actions based on
       event type
- 7.4: Return appropriate HTTP status codes (200 for success,
       400 for bad request)
"""

from typing import TYPE_CHECKING, Any

from app.enums import LogSeverity, WebhookEventType
from app.services.logging_service import LoggingService

if TYPE_CHECKING:
    from app.routers.webhook_router import WebhookEvent


class WebhookService:
    """Webhook business logic.

    Handles processing of incoming webhook events and routes them
    to appropriate handlers based on event type. All database operations
    are delegated to the appropriate DAOs via other services.
    """

    def __init__(self, log_service: LoggingService) -> None:
        """Initialize the webhook service.

        Args:
            log_service: Logging service for recording webhook activity.
        """
        self.log_service = log_service

    async def process_event(self, event: "WebhookEvent") -> dict[str, Any]:
        """Process webhook event.

        Routes the event to the appropriate handler based on event type.
        Uses pattern matching for clean event type dispatch.

        Args:
            event: WebhookEvent containing event_type, payload,
                   timestamp, and source.

        Returns:
            Dictionary with processing result:
            - {"handled": True, ...} for successfully processed events
            - {"handled": False, "reason": "..."} for unhandled events

        Requirements:
            - 7.3: Process webhook events and take appropriate actions
                   based on event type
        """
        match event.event_type:
            case WebhookEventType.TASK_CREATED:
                return await self._handle_task_created(event.payload)
            case WebhookEventType.EXTERNAL_TRIGGER:
                return await self._handle_external_trigger(event.payload)
            case _:
                return {"handled": False, "reason": "unknown_event_type"}

    async def _handle_task_created(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle task creation webhook.

        Processes task_created events by validating required fields
        and logging the event.

        Args:
            payload: Event payload containing:
                - user_id: User identifier (required)
                - title: Task title (required)
                - Additional optional fields

        Returns:
            Dictionary with result:
            - {"handled": True, "task_title": "..."} on success
            - {"handled": False, "reason": "missing_required_fields"}
              if validation fails

        Requirements:
            - 7.2: Pass webhook events to the Agent for processing
        """
        user_id = payload.get("user_id")
        task_title = payload.get("title")

        if not user_id or not task_title:
            return {"handled": False, "reason": "missing_required_fields"}

        await self.log_service.log_action(
            user_id=user_id,
            action=f"Webhook: task_created - {task_title}",
            severity=LogSeverity.INFO,
            details=payload,
        )

        return {"handled": True, "task_title": task_title}

    async def _handle_external_trigger(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle external trigger webhook.

        Processes external_trigger events. These are generic events
        from external systems that may trigger agent actions.

        Args:
            payload: Event payload with trigger-specific data.

        Returns:
            Dictionary indicating the event was received:
            {"handled": True, "payload_received": True}

        Requirements:
            - 7.3: Process webhook events and take appropriate actions
        """
        return {"handled": True, "payload_received": True}
