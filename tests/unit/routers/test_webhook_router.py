"""Unit tests for webhook router.

Tests HTTP endpoint behavior, request validation,
and proper delegation to WebhookService.

Requirements: 14.9 - Tests for Backend_API endpoints
Requirements: 7.1, 7.4 - Webhook endpoint and HTTP status codes
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.enums import WebhookEventType
from app.routers.webhook_router import create_webhook_router


@pytest.fixture
def mock_webhook_service():
    """Create a mock WebhookService."""
    service = AsyncMock()
    return service


@pytest.fixture
def client(mock_webhook_service):
    """Create test client with webhook router."""
    app = FastAPI()
    router = create_webhook_router(mock_webhook_service)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def whitelisted_client(mock_webhook_service):
    """Create test client with webhook router + whitelist enabled."""
    app = FastAPI()
    router = create_webhook_router(mock_webhook_service, allowed_users=["user-123"])
    app.include_router(router)
    return TestClient(app)


class TestHandleWebhook:
    """Tests for POST /webhook endpoint."""

    def test_webhook_task_created_success(self, client, mock_webhook_service):
        """Test successful task_created webhook processing."""
        mock_webhook_service.process_event.return_value = {
            "handled": True,
            "task_title": "New Task",
        }

        response = client.post(
            "/webhook",
            json={
                "eventType": "task_created",
                "payload": {"user_id": "user-123", "title": "New Task"},
                "timestamp": datetime.now().isoformat(),
                "source": "external-system",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["result"]["handled"] is True
        mock_webhook_service.process_event.assert_called_once()

    def test_webhook_external_trigger_success(self, client, mock_webhook_service):
        """Test successful external_trigger webhook processing."""
        mock_webhook_service.process_event.return_value = {
            "handled": True,
            "payload_received": True,
        }

        response = client.post(
            "/webhook",
            json={
                "eventType": "external_trigger",
                "payload": {"action": "refresh"},
                "timestamp": datetime.now().isoformat(),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["result"]["handled"] is True

    def test_webhook_invalid_event_type(self, client, mock_webhook_service):
        """Test webhook with invalid event type returns 400.

        The router catches ValueError from Pydantic validation and returns 400.
        """
        response = client.post(
            "/webhook",
            json={
                "eventType": "invalid_type",
                "payload": {},
                "timestamp": datetime.now().isoformat(),
            },
        )

        # Router catches ValueError and returns 400
        assert response.status_code == 400

    def test_webhook_missing_required_fields(self, client, mock_webhook_service):
        """Test webhook with missing required fields returns 400.

        The router catches ValueError from Pydantic validation and returns 400.
        """
        response = client.post(
            "/webhook",
            json={
                "eventType": "task_created",
                # Missing payload and timestamp
            },
        )

        # Router catches ValueError and returns 400
        assert response.status_code == 400

    def test_webhook_service_value_error(self, client, mock_webhook_service):
        """Test webhook returns 400 when service raises ValueError."""
        mock_webhook_service.process_event.side_effect = ValueError("Invalid payload format")

        response = client.post(
            "/webhook",
            json={
                "eventType": "task_created",
                "payload": {},
                "timestamp": datetime.now().isoformat(),
            },
        )

        assert response.status_code == 400
        assert "Invalid payload format" in response.json()["detail"]

    def test_webhook_service_exception(self, client, mock_webhook_service):
        """Test webhook returns 500 when service raises unexpected error."""
        mock_webhook_service.process_event.side_effect = RuntimeError("Unexpected error")

        response = client.post(
            "/webhook",
            json={
                "eventType": "task_created",
                "payload": {},
                "timestamp": datetime.now().isoformat(),
            },
        )

        assert response.status_code == 500
        assert "Unexpected error" in response.json()["detail"]

    def test_webhook_without_optional_source(self, client, mock_webhook_service):
        """Test webhook works without optional source field."""
        mock_webhook_service.process_event.return_value = {"handled": True}

        response = client.post(
            "/webhook",
            json={
                "eventType": "external_trigger",
                "payload": {"data": "test"},
                "timestamp": datetime.now().isoformat(),
                # source is optional
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_webhook_rejected_when_not_whitelisted(self, whitelisted_client, mock_webhook_service):
        response = whitelisted_client.post(
            "/webhook",
            json={
                "eventType": "task_created",
                "payload": {"user_id": "user-denied", "title": "New Task"},
                "timestamp": datetime.now().isoformat(),
                "source": "external-system",
            },
        )

        assert response.status_code == 403
        assert "contact iliag@sela.co.il" in response.json()["detail"]
        mock_webhook_service.process_event.assert_not_called()
