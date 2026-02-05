"""Unit tests for task router.

Tests HTTP endpoint behavior, request/response validation,
and proper delegation to TaskService.

Requirements: 14.9 - Tests for Backend_API endpoints (task CRUD operations)
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.enums import TaskStatus
from app.models.domain import Task
from app.routers.task_router import create_task_router


@pytest.fixture
def mock_task_service():
    """Create a mock TaskService."""
    service = AsyncMock()
    return service


@pytest.fixture
def client(mock_task_service):
    """Create test client with task router."""
    app = FastAPI()
    router = create_task_router(mock_task_service)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def whitelisted_client(mock_task_service):
    """Create test client with task router + whitelist enabled."""
    app = FastAPI()
    router = create_task_router(mock_task_service, allowed_users=["user-allowed"])
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def sample_task():
    """Create a sample task for testing."""
    return Task(
        id="task-123",
        user_id="user-456",
        title="Test Task",
        description="Test description",
        status=TaskStatus.PENDING,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


class TestGetTasks:
    """Tests for GET /api/tasks/{user_id} endpoint."""

    def test_get_tasks_returns_grouped_tasks(self, client, mock_task_service, sample_task):
        """Test that tasks are returned grouped by status."""
        mock_task_service.get_tasks_grouped_by_status.return_value = {
            "pending": [sample_task],
            "in_progress": [],
            "done": [],
        }

        response = client.get("/api/tasks/user-456")

        assert response.status_code == 200
        data = response.json()
        assert "pending" in data
        assert "inProgress" in data  # camelCase in JSON
        assert "done" in data
        assert len(data["pending"]) == 1
        mock_task_service.get_tasks_grouped_by_status.assert_called_once_with("user-456")

    def test_get_tasks_empty_lists(self, client, mock_task_service):
        """Test response when user has no tasks."""
        mock_task_service.get_tasks_grouped_by_status.return_value = {
            "pending": [],
            "in_progress": [],
            "done": [],
        }

        response = client.get("/api/tasks/user-789")

        assert response.status_code == 200
        data = response.json()
        assert data["pending"] == []
        assert data["inProgress"] == []
        assert data["done"] == []

    def test_get_tasks_rejected_when_not_whitelisted(self, whitelisted_client, mock_task_service):
        response = whitelisted_client.get("/api/tasks/user-denied")
        assert response.status_code == 403
        assert "contact iliag@sela.co.il" in response.json()["detail"]
        mock_task_service.get_tasks_grouped_by_status.assert_not_called()


class TestCreateTask:
    """Tests for POST /api/tasks endpoint."""

    def test_create_task_success(self, client, mock_task_service, sample_task):
        """Test successful task creation."""
        mock_task_service.create_task.return_value = sample_task

        response = client.post(
            "/api/tasks",
            json={
                "userId": "user-456",
                "title": "Test Task",
                "description": "Test description",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["taskId"] == "task-123"
        assert data["status"] == "created"
        mock_task_service.create_task.assert_called_once_with(
            user_id="user-456",
            title="Test Task",
            description="Test description",
        )

    def test_create_task_validation_error(self, client, mock_task_service):
        """Test task creation with validation error."""
        mock_task_service.create_task.side_effect = ValueError("Task title cannot be empty")

        response = client.post(
            "/api/tasks",
            json={"userId": "user-456", "title": "", "description": ""},
        )

        assert response.status_code == 400
        assert "Task title cannot be empty" in response.json()["detail"]

    def test_create_task_user_not_found(self, client, mock_task_service):
        """Test task creation when user doesn't exist."""
        mock_task_service.create_task.side_effect = ValueError("User user-999 not found")

        response = client.post(
            "/api/tasks",
            json={"userId": "user-999", "title": "Test", "description": ""},
        )

        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    def test_create_task_rejected_when_not_whitelisted(self, whitelisted_client, mock_task_service):
        response = whitelisted_client.post(
            "/api/tasks",
            json={
                "userId": "user-denied",
                "title": "Test Task",
                "description": "Test description",
            },
        )

        assert response.status_code == 403
        assert "contact iliag@sela.co.il" in response.json()["detail"]
        mock_task_service.create_task.assert_not_called()


class TestUpdateTaskStatus:
    """Tests for PATCH /api/tasks/{task_id}/status endpoint."""

    def test_update_status_success(self, client, mock_task_service):
        """Test successful status update."""
        mock_task_service.update_task_status.return_value = True

        response = client.patch(
            "/api/tasks/task-123/status?user_id=user-456",
            json={"status": "in_progress"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["taskId"] == "task-123"
        assert data["status"] == "updated"
        mock_task_service.update_task_status.assert_called_once_with(
            task_id="task-123",
            status=TaskStatus.IN_PROGRESS,
            user_id="user-456",
        )

    def test_update_status_task_not_found(self, client, mock_task_service):
        """Test status update when task doesn't exist."""
        mock_task_service.update_task_status.side_effect = ValueError("Task task-999 not found")

        response = client.patch(
            "/api/tasks/task-999/status?user_id=user-456",
            json={"status": "done"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_update_status_permission_denied(self, client, mock_task_service):
        """Test status update when user doesn't own task."""
        mock_task_service.update_task_status.side_effect = PermissionError(
            "Cannot update another user's task"
        )

        response = client.patch(
            "/api/tasks/task-123/status?user_id=wrong-user",
            json={"status": "done"},
        )

        assert response.status_code == 403
        assert "Cannot update" in response.json()["detail"]

    def test_update_status_missing_user_id(self, client, mock_task_service):
        """Test status update without user_id query param."""
        response = client.patch(
            "/api/tasks/task-123/status",
            json={"status": "done"},
        )

        assert response.status_code == 422  # Validation error

    def test_update_status_rejected_when_not_whitelisted(
        self, whitelisted_client, mock_task_service
    ):
        response = whitelisted_client.patch(
            "/api/tasks/task-123/status?user_id=user-denied",
            json={"status": "done"},
        )
        assert response.status_code == 403
        assert "contact iliag@sela.co.il" in response.json()["detail"]
        mock_task_service.update_task_status.assert_not_called()
