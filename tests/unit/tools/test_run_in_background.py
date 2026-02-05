"""Unit tests for run_in_background tool.

Tests the background task spawning tool including:
- Input validation (empty command, empty description)
- Error handling when callback is not set
- Successful task spawning
- Callback failure handling
"""

from unittest.mock import MagicMock

import pytest

from app.tools.run_in_background import (
    clear_background_task_context,
    run_in_background,
    set_background_task_context,
)


@pytest.fixture(autouse=True)
def reset_context():
    """Reset context before and after each test."""
    clear_background_task_context()
    yield
    clear_background_task_context()


class TestSetBackgroundTaskContext:
    """Tests for set_background_task_context function."""

    def test_sets_callback_and_user_id(self):
        """Should set spawn callback and user_id."""
        from app.tools import run_in_background as module

        mock_callback = MagicMock(return_value=True)
        set_background_task_context(mock_callback, "user-123")

        # Verify context is set by calling the tool
        result = run_in_background(
            command="echo hello",
            description="Test command",
        )

        assert "Background task started successfully" in result
        mock_callback.assert_called_once()


class TestClearBackgroundTaskContext:
    """Tests for clear_background_task_context function."""

    def test_clears_context(self):
        """Should clear the callback context."""
        mock_callback = MagicMock(return_value=True)
        set_background_task_context(mock_callback, "user-123")
        clear_background_task_context()

        result = run_in_background(
            command="echo hello",
            description="Test command",
        )

        assert "not available in this context" in result


class TestRunInBackground:
    """Tests for run_in_background tool function."""

    def test_returns_error_when_command_empty(self):
        """Should return error when command is empty."""
        result = run_in_background(command="", description="Test task")
        assert "No command provided" in result

    def test_returns_error_when_command_whitespace(self):
        """Should return error when command is only whitespace."""
        result = run_in_background(command="   ", description="Test task")
        assert "No command provided" in result

    def test_returns_error_when_description_empty(self):
        """Should return error when description is empty."""
        result = run_in_background(command="echo hello", description="")
        assert "No description provided" in result

    def test_returns_error_when_description_whitespace(self):
        """Should return error when description is only whitespace."""
        result = run_in_background(command="echo hello", description="   ")
        assert "No description provided" in result

    def test_returns_error_when_callback_not_set(self):
        """Should return error when spawn callback is not available."""
        result = run_in_background(
            command="echo hello",
            description="Test command",
        )
        assert "not available in this context" in result
        assert "shell tool" in result

    def test_returns_success_when_task_spawned(self):
        """Should return success message when task is spawned."""
        mock_callback = MagicMock(return_value=True)
        set_background_task_context(mock_callback, "user-123")

        result = run_in_background(
            command="uv run python research.py",
            description="Deep research on quantum computing",
        )

        assert "Background task started successfully" in result
        assert "Task ID:" in result
        assert "Deep research on quantum computing" in result
        mock_callback.assert_called_once()

        # Verify callback was called with correct arguments
        call_args = mock_callback.call_args
        assert call_args[0][1] == "uv run python research.py"  # command
        assert call_args[0][2] == "Deep research on quantum computing"  # description
        assert call_args[0][3] is None  # work_dir

    def test_passes_work_dir_to_callback(self):
        """Should pass work_dir to the callback."""
        mock_callback = MagicMock(return_value=True)
        set_background_task_context(mock_callback, "user-123")

        run_in_background(
            command="./build.sh",
            description="Build project",
            work_dir="/home/user/project",
        )

        call_args = mock_callback.call_args
        assert call_args[0][3] == "/home/user/project"  # work_dir

    def test_returns_error_when_callback_returns_false(self):
        """Should return error when callback indicates failure (e.g., limit reached)."""
        mock_callback = MagicMock(return_value=False)
        set_background_task_context(mock_callback, "user-123")

        result = run_in_background(
            command="echo hello",
            description="Test command",
        )

        assert "Could not start background task" in result
        assert "maximum number of concurrent background tasks" in result

    def test_returns_error_when_callback_raises_exception(self):
        """Should return error when callback raises an exception."""
        mock_callback = MagicMock(side_effect=RuntimeError("Connection failed"))
        set_background_task_context(mock_callback, "user-123")

        result = run_in_background(
            command="echo hello",
            description="Test command",
        )

        assert "Failed to start background task" in result
        assert "Connection failed" in result

    def test_generates_unique_task_id(self):
        """Should generate a unique task ID for each call."""
        mock_callback = MagicMock(return_value=True)
        set_background_task_context(mock_callback, "user-123")

        run_in_background(command="echo 1", description="Task 1")
        run_in_background(command="echo 2", description="Task 2")

        # Get the task IDs from the two calls
        task_id_1 = mock_callback.call_args_list[0][0][0]
        task_id_2 = mock_callback.call_args_list[1][0][0]

        assert task_id_1 != task_id_2
        assert len(task_id_1) == 8
        assert len(task_id_2) == 8

    def test_strips_command_and_description(self):
        """Should strip whitespace from command and description."""
        mock_callback = MagicMock(return_value=True)
        set_background_task_context(mock_callback, "user-123")

        run_in_background(
            command="  echo hello  ",
            description="  Test command  ",
        )

        call_args = mock_callback.call_args
        assert call_args[0][1] == "echo hello"  # stripped command
        assert call_args[0][2] == "Test command"  # stripped description
