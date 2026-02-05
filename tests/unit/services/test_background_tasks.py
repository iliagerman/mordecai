"""Unit tests for BackgroundTaskManager.

Tests verify:
- Task spawning and tracking
- Per-user limit enforcement
- Task cancellation
- Task listing
- Cleanup after completion
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.background_tasks import BackgroundTaskInfo, BackgroundTaskManager


@pytest.fixture
def manager():
    """Create a BackgroundTaskManager with mocked dependencies."""
    return BackgroundTaskManager(
        agent_service=None,
        response_callback=None,
        logging_service=None,
        max_per_user=3,
    )


class TestBackgroundTaskInfo:
    """Tests for BackgroundTaskInfo dataclass."""

    def test_creates_with_required_fields(self):
        """Should create info with required fields."""
        info = BackgroundTaskInfo(
            task_id="abc123",
            user_id="user-1",
            chat_id=12345,
            description="Test task",
            command="echo hello",
            work_dir=None,
        )

        assert info.task_id == "abc123"
        assert info.user_id == "user-1"
        assert info.chat_id == 12345
        assert info.description == "Test task"
        assert info.command == "echo hello"
        assert info.work_dir is None
        assert info.asyncio_task is None
        assert info.process is None

    def test_started_at_defaults_to_now(self):
        """Should have started_at defaulted to current UTC time."""
        from datetime import datetime, UTC

        before = datetime.now(UTC)
        info = BackgroundTaskInfo(
            task_id="abc123",
            user_id="user-1",
            chat_id=12345,
            description="Test task",
            command="echo hello",
            work_dir=None,
        )
        after = datetime.now(UTC)

        assert before <= info.started_at <= after


class TestBackgroundTaskManager:
    """Tests for BackgroundTaskManager class."""

    def test_count_active_returns_zero_initially(self, manager):
        """Should return 0 when no tasks are active."""
        assert manager.count_active("user-1") == 0

    def test_list_active_returns_empty_initially(self, manager):
        """Should return empty list when no tasks are active."""
        assert manager.list_active("user-1") == []

    def test_set_agent_service(self, manager):
        """Should set the agent service."""
        mock_service = MagicMock()
        manager.set_agent_service(mock_service)
        assert manager._agent_service is mock_service

    def test_set_response_callback(self, manager):
        """Should set the response callback."""
        mock_callback = AsyncMock()
        manager.set_response_callback(mock_callback)
        assert manager._response_callback is mock_callback


class TestSpawn:
    """Tests for spawn method."""

    @pytest.mark.asyncio
    async def test_spawn_returns_true_when_successful(self, manager):
        """Should return True when task is spawned."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            result = manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="echo hello",
                description="Test task",
                work_dir=None,
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_spawn_tracks_task(self, manager):
        """Should track the spawned task."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="echo hello",
                description="Test task",
                work_dir=None,
            )

        assert manager.count_active("user-1") == 1
        tasks = manager.list_active("user-1")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "task-1"
        assert tasks[0]["description"] == "Test task"

    @pytest.mark.asyncio
    async def test_spawn_respects_max_per_user(self, manager):
        """Should reject spawn when user reaches max limit."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            # Spawn max_per_user (3) tasks
            for i in range(3):
                result = manager.spawn(
                    user_id="user-1",
                    chat_id=12345,
                    task_id=f"task-{i}",
                    command=f"echo {i}",
                    description=f"Task {i}",
                    work_dir=None,
                )
                assert result is True

            # Fourth task should be rejected
            result = manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-4",
                command="echo 4",
                description="Task 4",
                work_dir=None,
            )

        assert result is False
        assert manager.count_active("user-1") == 3

    @pytest.mark.asyncio
    async def test_spawn_allows_different_users(self, manager):
        """Should allow tasks from different users independently."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="echo hello",
                description="User 1 task",
                work_dir=None,
            )
            manager.spawn(
                user_id="user-2",
                chat_id=67890,
                task_id="task-2",
                command="echo world",
                description="User 2 task",
                work_dir=None,
            )

        assert manager.count_active("user-1") == 1
        assert manager.count_active("user-2") == 1

    def test_spawn_with_loop_uses_call_soon_threadsafe(self, manager):
        """Should use call_soon_threadsafe when loop is provided."""
        mock_loop = MagicMock()

        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="echo hello",
                description="Test task",
                work_dir=None,
                loop=mock_loop,
            )

        mock_loop.call_soon_threadsafe.assert_called_once()


class TestCancel:
    """Tests for cancel method."""

    @pytest.mark.asyncio
    async def test_cancel_returns_false_for_nonexistent_task(self, manager):
        """Should return False when task doesn't exist."""
        result = await manager.cancel("user-1", "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_returns_true_for_existing_task(self, manager):
        """Should return True when task is cancelled."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="sleep 60",
                description="Long task",
                work_dir=None,
            )

        result = await manager.cancel("user-1", "task-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_kills_subprocess(self, manager):
        """Should kill the subprocess if running."""
        mock_process = MagicMock()

        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="sleep 60",
                description="Long task",
                work_dir=None,
            )

        # Manually set the process
        manager._active["user-1"]["task-1"].process = mock_process

        await manager.cancel("user-1", "task-1")
        mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_cancels_asyncio_task(self, manager):
        """Should cancel the asyncio task."""
        mock_asyncio_task = MagicMock()

        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="sleep 60",
                description="Long task",
                work_dir=None,
            )

        # Manually set the asyncio task
        manager._active["user-1"]["task-1"].asyncio_task = mock_asyncio_task

        await manager.cancel("user-1", "task-1")
        mock_asyncio_task.cancel.assert_called_once()


class TestCancelAll:
    """Tests for cancel_all method."""

    @pytest.mark.asyncio
    async def test_cancel_all_returns_count(self, manager):
        """Should return count of cancelled tasks."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn("user-1", 12345, "task-1", "echo 1", "Task 1", None)
            manager.spawn("user-1", 12345, "task-2", "echo 2", "Task 2", None)
            manager.spawn("user-2", 67890, "task-3", "echo 3", "Task 3", None)

        count = await manager.cancel_all()
        assert count == 3

    @pytest.mark.asyncio
    async def test_cancel_all_returns_zero_when_no_tasks(self, manager):
        """Should return 0 when no tasks to cancel."""
        count = await manager.cancel_all()
        assert count == 0


class TestListActive:
    """Tests for list_active method."""

    @pytest.mark.asyncio
    async def test_list_active_returns_task_info(self, manager):
        """Should return list with task details."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn(
                user_id="user-1",
                chat_id=12345,
                task_id="task-1",
                command="echo hello",
                description="Test task",
                work_dir=None,
            )

        tasks = manager.list_active("user-1")

        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "task-1"
        assert tasks[0]["description"] == "Test task"
        assert "started_at" in tasks[0]

    @pytest.mark.asyncio
    async def test_list_active_only_returns_user_tasks(self, manager):
        """Should only return tasks for the specified user."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn("user-1", 12345, "task-1", "echo 1", "User 1 task", None)
            manager.spawn("user-2", 67890, "task-2", "echo 2", "User 2 task", None)

        tasks = manager.list_active("user-1")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "task-1"


class TestExecute:
    """Tests for _execute method (integration-style)."""

    @pytest.mark.asyncio
    async def test_execute_runs_command_and_captures_output(self):
        """Should run command and capture stdout/stderr."""
        manager = BackgroundTaskManager()
        mock_response_callback = AsyncMock()
        manager.set_response_callback(mock_response_callback)

        info = BackgroundTaskInfo(
            task_id="test-1",
            user_id="user-1",
            chat_id=12345,
            description="Echo test",
            command="echo 'hello world'",
            work_dir=None,
        )

        await manager._execute(info)

        # Should have called response callback with the result
        mock_response_callback.assert_called_once()
        call_args = mock_response_callback.call_args
        assert call_args[0][0] == 12345  # chat_id
        assert "hello world" in call_args[0][1] or "Echo test" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_execute_processes_through_agent_when_available(self):
        """Should invoke agent service to process results."""
        import tempfile

        mock_agent = MagicMock()
        mock_agent.process_message = AsyncMock(return_value="Agent summary")
        # Mock _get_user_working_dir to return a valid path
        mock_agent._get_user_working_dir = MagicMock(return_value=tempfile.gettempdir())

        manager = BackgroundTaskManager(agent_service=mock_agent)
        mock_response_callback = AsyncMock()
        manager.set_response_callback(mock_response_callback)

        info = BackgroundTaskInfo(
            task_id="test-1",
            user_id="user-1",
            chat_id=12345,
            description="Echo test",
            command="echo 'hello'",
            work_dir=None,
        )

        await manager._execute(info)

        mock_agent.process_message.assert_called_once()
        call_args = mock_agent.process_message.call_args
        assert call_args.kwargs["user_id"] == "user-1"
        assert "[Background Task Result]" in call_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_execute_sends_notification_on_error(self):
        """Should send error notification when command fails."""
        manager = BackgroundTaskManager()
        mock_response_callback = AsyncMock()
        manager.set_response_callback(mock_response_callback)

        info = BackgroundTaskInfo(
            task_id="test-1",
            user_id="user-1",
            chat_id=12345,
            description="Failing command",
            command="exit 1",
            work_dir=None,
        )

        await manager._execute(info)

        mock_response_callback.assert_called_once()
        call_args = mock_response_callback.call_args
        # Should indicate failure
        message = call_args[0][1]
        assert "failed" in message.lower() or "exit code" in message.lower()

    @pytest.mark.asyncio
    async def test_execute_handles_cancelled_task(self):
        """Should send cancellation notification when task is cancelled."""
        manager = BackgroundTaskManager()
        mock_response_callback = AsyncMock()
        manager.set_response_callback(mock_response_callback)

        info = BackgroundTaskInfo(
            task_id="test-1",
            user_id="user-1",
            chat_id=12345,
            description="Long running task",
            command="sleep 60",
            work_dir=None,
        )

        # Start execute in a task
        async def run_and_cancel():
            task = asyncio.create_task(manager._execute(info))
            await asyncio.sleep(0.1)  # Let it start
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_and_cancel()

        # Should have sent cancellation notification
        mock_response_callback.assert_called()
        call_args = mock_response_callback.call_args
        assert "cancelled" in call_args[0][1].lower()


class TestCleanupTask:
    """Tests for _cleanup_task method."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_task_from_active(self, manager):
        """Should remove task from active tracking."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn("user-1", 12345, "task-1", "echo 1", "Task 1", None)

        assert manager.count_active("user-1") == 1

        manager._cleanup_task("user-1", "task-1")

        assert manager.count_active("user-1") == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_user_entry_when_empty(self, manager):
        """Should remove user entry when no more tasks."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn("user-1", 12345, "task-1", "echo 1", "Task 1", None)

        manager._cleanup_task("user-1", "task-1")

        assert "user-1" not in manager._active

    def test_cleanup_handles_nonexistent_user(self, manager):
        """Should handle cleanup for nonexistent user gracefully."""
        manager._cleanup_task("nonexistent-user", "task-1")
        # Should not raise

    @pytest.mark.asyncio
    async def test_cleanup_handles_nonexistent_task(self, manager):
        """Should handle cleanup for nonexistent task gracefully."""
        with patch.object(manager, "_execute", new_callable=AsyncMock):
            manager.spawn("user-1", 12345, "task-1", "echo 1", "Task 1", None)

        manager._cleanup_task("user-1", "nonexistent-task")
        # Should not raise, and original task should still exist
        assert manager.count_active("user-1") == 1
