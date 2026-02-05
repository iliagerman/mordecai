"""System scheduler for background system tasks.

This module provides the SystemScheduler that runs system-level
periodic tasks like file cleanup.

Requirements:
- 10.5: Schedule hourly file cleanup
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.services.file_service import FileService

logger = logging.getLogger(__name__)


class SystemScheduler:
    """Scheduler for system-level periodic tasks.

    Runs system tasks on a fixed schedule, separate from user-created
    cron tasks. Currently handles file cleanup.

    Requirements:
        - 10.5: Schedule hourly file cleanup
    """

    # File cleanup runs every hour (3600 seconds)
    FILE_CLEANUP_INTERVAL_SECONDS = 3600

    def __init__(
        self,
        config: "AgentConfig",
        file_service: "FileService",
    ) -> None:
        """Initialize the system scheduler.

        Args:
            config: Application configuration.
            file_service: FileService for cleanup operations.
        """
        self.config = config
        self.file_service = file_service
        self._running = False
        self._cleanup_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        logger.info("SystemScheduler initialized")

    async def start(self) -> None:
        """Start the system scheduler.

        Begins the file cleanup background task.

        Requirements:
            - 10.5: Schedule hourly file cleanup
        """
        if self._running:
            logger.warning("SystemScheduler is already running")
            return

        self._running = True
        self._stop_event.clear()
        self._cleanup_task = asyncio.create_task(
            self._run_file_cleanup_loop()
        )

        logger.info(
            "SystemScheduler started, file cleanup every %d seconds",
            self.FILE_CLEANUP_INTERVAL_SECONDS,
        )

    async def stop(self) -> None:
        """Stop the system scheduler gracefully."""
        if not self._running:
            logger.warning("SystemScheduler is not running")
            return

        logger.info("Stopping SystemScheduler...")
        self._running = False
        self._stop_event.set()

        if self._cleanup_task:
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "SystemScheduler task did not stop gracefully, cancelling"
                )
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            finally:
                self._cleanup_task = None

        logger.info("SystemScheduler stopped")

    async def _run_file_cleanup_loop(self) -> None:
        """Main loop for file cleanup task.

        Runs the cleanup task every hour (at the top of the hour).

        Requirements:
            - 10.5: Schedule hourly file cleanup (cron: "0 * * * *")
        """
        logger.info("File cleanup loop started")

        while self._running:
            try:
                await self._execute_file_cleanup()
            except Exception as e:
                logger.exception("Error in file cleanup loop: %s", e)

            # Wait for the interval or until stop is signaled
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.FILE_CLEANUP_INTERVAL_SECONDS,
                )
                # If we get here, stop was signaled
                break
            except asyncio.TimeoutError:
                # Normal timeout, continue the loop
                continue

        logger.info("File cleanup loop ended")

    async def _execute_file_cleanup(self) -> None:
        """Execute the file cleanup task.

        Deletes files older than the configured retention period.

        Requirements:
            - 4.6: Clean up temporary files after retention period
            - 10.5: Delete files older than 24 hours from working folders
        """
        from app.scheduler.file_cleanup_task import file_cleanup_task

        try:
            deleted_count = await file_cleanup_task(
                file_service=self.file_service,
                config=self.config,
            )
            logger.info(
                "File cleanup: deleted %d old files",
                deleted_count,
            )
        except Exception as e:
            logger.error("File cleanup failed: %s", e)

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is currently running.

        Returns:
            True if the scheduler is running, False otherwise.
        """
        return self._running
