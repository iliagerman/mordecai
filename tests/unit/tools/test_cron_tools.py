"""Unit tests for cron_tools.

Tests the cron task management tools including:
- create_cron_task validation and success cases
- list_cron_tasks functionality
- delete_cron_task functionality
- _run_async helper for async/sync bridging
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.cron_tools import (
    create_cron_task,
    delete_cron_task,
    list_cron_tasks,
    set_cron_context,
    _run_async,
)


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    import app.tools.cron_tools as module
    module._cron_service = None
    module._current_user_id = None
    yield
    module._cron_service = None
    module._current_user_id = None


class TestSetCronContext:
    """Tests for set_cron_context function."""

    def test_sets_cron_service_and_user_id(self):
        """Should set cron service and user_id."""
        import app.tools.cron_tools as module
        mock_service = MagicMock()

        set_cron_context(mock_service, "user-123")

        assert module._cron_service is mock_service
        assert module._current_user_id == "user-123"


class TestRunAsync:
    """Tests for _run_async helper function."""

    def test_run_async_without_running_loop(self):
        """Should run coroutine when no event loop is running."""
        async def sample_coro():
            return "result"

        result = _run_async(sample_coro())
        assert result == "result"

    @pytest.mark.asyncio
    async def test_run_async_with_running_loop(self):
        """Should run coroutine within existing event loop using nest_asyncio."""
        async def sample_coro():
            return "nested_result"

        # This tests the nest_asyncio path
        result = _run_async(sample_coro())
        assert result == "nested_result"


class TestCreateCronTask:
    """Tests for create_cron_task tool function."""

    def test_returns_error_when_name_empty(self):
        """Should return error when name is empty."""
        result = create_cron_task(
            name="",
            instructions="do something",
            cron_expression="0 6 * * *"
        )
        assert "Please provide a name" in result

    def test_returns_error_when_name_whitespace(self):
        """Should return error when name is only whitespace."""
        result = create_cron_task(
            name="   ",
            instructions="do something",
            cron_expression="0 6 * * *"
        )
        assert "Please provide a name" in result

    def test_returns_error_when_instructions_empty(self):
        """Should return error when instructions is empty."""
        result = create_cron_task(
            name="test-task",
            instructions="",
            cron_expression="0 6 * * *"
        )
        assert "Please provide instructions" in result

    def test_returns_error_when_cron_expression_empty(self):
        """Should return error when cron_expression is empty."""
        result = create_cron_task(
            name="test-task",
            instructions="do something",
            cron_expression=""
        )
        assert "Please provide a cron expression" in result

    def test_returns_error_when_cron_service_not_set(self):
        """Should return error when cron service is not available."""
        result = create_cron_task(
            name="test-task",
            instructions="do something",
            cron_expression="0 6 * * *"
        )
        assert "Cron service not available" in result

    def test_returns_error_when_user_id_not_set(self):
        """Should return error when user context is not available."""
        import app.tools.cron_tools as module
        module._cron_service = MagicMock()

        result = create_cron_task(
            name="test-task",
            instructions="do something",
            cron_expression="0 6 * * *"
        )
        assert "User context not available" in result

    def test_returns_success_when_task_created(self):
        """Should return success message when task is created."""
        import app.tools.cron_tools as module
        from app.models.domain import CronTask

        mock_task = CronTask(
            id="task-123",
            user_id="user-123",
            name="daily-reminder",
            instructions="remind me",
            cron_expression="0 6 * * *",
            enabled=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            last_executed_at=None,
            next_execution_at=datetime(2026, 1, 30, 6, 0, 0),
        )

        mock_service = MagicMock()
        mock_service.create_task = AsyncMock(return_value=mock_task)
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = create_cron_task(
            name="daily-reminder",
            instructions="remind me",
            cron_expression="0 6 * * *"
        )

        assert "✅ Created scheduled task" in result
        assert "daily-reminder" in result
        assert "remind me" in result
        assert "0 6 * * *" in result
        mock_service.create_task.assert_called_once()

    def test_returns_error_on_invalid_cron_expression(self):
        """Should return error for invalid cron expression."""
        import app.tools.cron_tools as module
        from app.services.cron_service import CronExpressionError

        mock_service = MagicMock()
        mock_service.create_task = AsyncMock(
            side_effect=CronExpressionError("Invalid field")
        )
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = create_cron_task(
            name="test-task",
            instructions="do something",
            cron_expression="invalid"
        )

        assert "Invalid cron expression" in result
        assert "minute hour day month weekday" in result

    def test_returns_error_on_duplicate_task(self):
        """Should return error when task name already exists."""
        import app.tools.cron_tools as module
        from app.services.cron_service import CronTaskDuplicateError

        mock_service = MagicMock()
        mock_service.create_task = AsyncMock(
            side_effect=CronTaskDuplicateError("Duplicate")
        )
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = create_cron_task(
            name="existing-task",
            instructions="do something",
            cron_expression="0 6 * * *"
        )

        assert "already exists" in result
        assert "existing-task" in result


class TestListCronTasks:
    """Tests for list_cron_tasks tool function."""

    def test_returns_error_when_cron_service_not_set(self):
        """Should return error when cron service is not available."""
        result = list_cron_tasks()
        assert "Cron service not available" in result

    def test_returns_error_when_user_id_not_set(self):
        """Should return error when user context is not available."""
        import app.tools.cron_tools as module
        module._cron_service = MagicMock()

        result = list_cron_tasks()
        assert "User context not available" in result

    def test_returns_no_tasks_message_when_empty(self):
        """Should return message when no tasks found."""
        import app.tools.cron_tools as module

        mock_service = MagicMock()
        mock_service.list_tasks = AsyncMock(return_value=[])
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = list_cron_tasks()

        assert "No scheduled tasks found" in result

    def test_returns_formatted_task_list(self):
        """Should return formatted list of tasks."""
        import app.tools.cron_tools as module
        from app.models.domain import CronTask

        mock_tasks = [
            CronTask(
                id="task-1",
                user_id="user-123",
                name="morning-reminder",
                instructions="wake up",
                cron_expression="0 6 * * *",
                enabled=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                last_executed_at=datetime(2026, 1, 29, 6, 0, 0),
                next_execution_at=datetime(2026, 1, 30, 6, 0, 0),
            ),
            CronTask(
                id="task-2",
                user_id="user-123",
                name="weekly-report",
                instructions="generate report",
                cron_expression="0 9 * * 1",
                enabled=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                last_executed_at=None,
                next_execution_at=datetime(2026, 2, 3, 9, 0, 0),
            ),
        ]

        mock_service = MagicMock()
        mock_service.list_tasks = AsyncMock(return_value=mock_tasks)
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = list_cron_tasks()

        assert "Scheduled Tasks" in result
        assert "morning-reminder" in result
        assert "weekly-report" in result
        assert "✅ Enabled" in result
        assert "⏸️ Disabled" in result
        assert "wake up" in result
        assert "generate report" in result


class TestDeleteCronTask:
    """Tests for delete_cron_task tool function."""

    def test_returns_error_when_identifier_empty(self):
        """Should return error when task identifier is empty."""
        result = delete_cron_task(task_identifier="")
        assert "Please specify the name or ID" in result

    def test_returns_error_when_identifier_whitespace(self):
        """Should return error when identifier is only whitespace."""
        result = delete_cron_task(task_identifier="   ")
        assert "Please specify the name or ID" in result

    def test_returns_error_when_cron_service_not_set(self):
        """Should return error when cron service is not available."""
        result = delete_cron_task(task_identifier="test-task")
        assert "Cron service not available" in result

    def test_returns_error_when_user_id_not_set(self):
        """Should return error when user context is not available."""
        import app.tools.cron_tools as module
        module._cron_service = MagicMock()

        result = delete_cron_task(task_identifier="test-task")
        assert "User context not available" in result

    def test_returns_success_when_task_deleted(self):
        """Should return success message when task is deleted."""
        import app.tools.cron_tools as module

        mock_service = MagicMock()
        mock_service.delete_task = AsyncMock(return_value=True)
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = delete_cron_task(task_identifier="my-task")

        assert "✅ Deleted scheduled task" in result
        assert "my-task" in result
        mock_service.delete_task.assert_called_once_with(
            user_id="user-123",
            task_identifier="my-task",
        )

    def test_returns_error_when_task_not_found(self):
        """Should return error when task is not found."""
        import app.tools.cron_tools as module
        from app.services.cron_service import CronTaskNotFoundError

        mock_service = MagicMock()
        mock_service.delete_task = AsyncMock(
            side_effect=CronTaskNotFoundError("Not found")
        )
        module._cron_service = mock_service
        module._current_user_id = "user-123"

        result = delete_cron_task(task_identifier="nonexistent")

        assert "not found" in result
        assert "nonexistent" in result
