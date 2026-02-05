"""Tests for SystemScheduler and file cleanup task.

Tests the system-level scheduler that handles periodic tasks
like file cleanup.
"""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import AgentConfig
from app.scheduler.file_cleanup_task import file_cleanup_task
from app.scheduler.system_scheduler import SystemScheduler
from app.services.file_service import FileService


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as temp_base:
        with tempfile.TemporaryDirectory() as work_base:
            yield temp_base, work_base


@pytest.fixture
def config(temp_dirs):
    """Create test configuration with temp directories."""
    temp_base, work_base = temp_dirs
    return AgentConfig(
        telegram_bot_token="test_token",
        temp_files_base_dir=temp_base,
        working_folder_base_dir=work_base,
        file_retention_hours=24,
    )


@pytest.fixture
def file_service(config):
    """Create FileService instance for testing."""
    return FileService(config)


class TestFileCleanupTask:
    """Tests for the file cleanup task function."""

    async def test_cleanup_task_calls_file_service(
        self, config, file_service
    ):
        """Test that cleanup task calls file_service.cleanup_old_files."""
        with patch.object(
            file_service, "cleanup_old_files", new_callable=AsyncMock
        ) as mock_cleanup:
            mock_cleanup.return_value = 5

            result = await file_cleanup_task(file_service, config)

            mock_cleanup.assert_called_once_with(
                max_age_hours=config.file_retention_hours
            )
            assert result == 5

    async def test_cleanup_task_uses_config_retention_hours(
        self, temp_dirs
    ):
        """Test that cleanup uses file_retention_hours from config."""
        temp_base, work_base = temp_dirs
        config = AgentConfig(
            telegram_bot_token="test_token",
            temp_files_base_dir=temp_base,
            working_folder_base_dir=work_base,
            file_retention_hours=48,  # Custom retention
        )
        file_service = FileService(config)

        with patch.object(
            file_service, "cleanup_old_files", new_callable=AsyncMock
        ) as mock_cleanup:
            mock_cleanup.return_value = 0

            await file_cleanup_task(file_service, config)

            mock_cleanup.assert_called_once_with(max_age_hours=48)

    async def test_cleanup_task_returns_deleted_count(
        self, config, file_service
    ):
        """Test that cleanup task returns the count of deleted files."""
        with patch.object(
            file_service, "cleanup_old_files", new_callable=AsyncMock
        ) as mock_cleanup:
            mock_cleanup.return_value = 10

            result = await file_cleanup_task(file_service, config)

            assert result == 10


class TestSystemScheduler:
    """Tests for the SystemScheduler class."""

    async def test_scheduler_initializes(self, config, file_service):
        """Test that scheduler initializes correctly."""
        scheduler = SystemScheduler(config, file_service)

        assert scheduler.config == config
        assert scheduler.file_service == file_service
        assert not scheduler.is_running

    async def test_scheduler_starts_and_stops(self, config, file_service):
        """Test that scheduler can start and stop."""
        scheduler = SystemScheduler(config, file_service)

        # Start scheduler
        await scheduler.start()
        assert scheduler.is_running

        # Stop scheduler
        await scheduler.stop()
        assert not scheduler.is_running

    async def test_scheduler_start_is_idempotent(self, config, file_service):
        """Test that starting an already running scheduler is safe."""
        scheduler = SystemScheduler(config, file_service)

        await scheduler.start()
        await scheduler.start()  # Should not raise
        assert scheduler.is_running

        await scheduler.stop()

    async def test_scheduler_stop_is_idempotent(self, config, file_service):
        """Test that stopping a non-running scheduler is safe."""
        scheduler = SystemScheduler(config, file_service)

        await scheduler.stop()  # Should not raise
        assert not scheduler.is_running

    async def test_scheduler_executes_cleanup(self, config, file_service):
        """Test that scheduler executes file cleanup."""
        scheduler = SystemScheduler(config, file_service)
        # Override interval for faster testing
        scheduler.FILE_CLEANUP_INTERVAL_SECONDS = 0.1

        with patch.object(
            file_service, "cleanup_old_files", new_callable=AsyncMock
        ) as mock_cleanup:
            mock_cleanup.return_value = 3

            await scheduler.start()
            # Wait for at least one cleanup cycle
            await asyncio.sleep(0.2)
            await scheduler.stop()

            # Cleanup should have been called at least once
            assert mock_cleanup.call_count >= 1


class TestFileCleanupIntegration:
    """Integration tests for file cleanup functionality."""

    async def test_cleanup_deletes_old_files(self, config, file_service):
        """Test that cleanup actually deletes old files."""
        # Create user directories
        user_id = "test_user"
        temp_dir = file_service.get_user_temp_dir(user_id)
        work_dir = file_service.get_user_working_dir(user_id)

        # Create test files
        old_file_temp = temp_dir / "old_file.txt"
        old_file_work = work_dir / "old_file.txt"
        new_file_temp = temp_dir / "new_file.txt"

        old_file_temp.write_text("old content")
        old_file_work.write_text("old content")
        new_file_temp.write_text("new content")

        # Make files "old" by setting mtime to past
        old_time = time.time() - (25 * 3600)  # 25 hours ago
        old_file_temp.touch()
        old_file_work.touch()
        import os
        os.utime(old_file_temp, (old_time, old_time))
        os.utime(old_file_work, (old_time, old_time))

        # Run cleanup with 24 hour retention
        deleted = await file_service.cleanup_old_files(max_age_hours=24)

        # Old files should be deleted
        assert not old_file_temp.exists()
        assert not old_file_work.exists()
        # New file should remain
        assert new_file_temp.exists()
        # Should have deleted 2 files
        assert deleted == 2

    async def test_cleanup_preserves_recent_files(
        self, config, file_service
    ):
        """Test that cleanup preserves files within retention period."""
        user_id = "test_user"
        temp_dir = file_service.get_user_temp_dir(user_id)

        # Create recent file
        recent_file = temp_dir / "recent.txt"
        recent_file.write_text("recent content")

        # Run cleanup
        deleted = await file_service.cleanup_old_files(max_age_hours=24)

        # Recent file should remain
        assert recent_file.exists()
        assert deleted == 0
