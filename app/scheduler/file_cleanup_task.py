"""File cleanup task for scheduled execution.

This module provides the file cleanup task that runs hourly to delete
files older than the configured retention period.

Requirements:
- 4.6: Clean up temporary files after retention period
- 10.5: Delete files older than 24 hours from working folders
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.services.file_service import FileService

logger = logging.getLogger(__name__)


async def file_cleanup_task(
    file_service: "FileService",
    config: "AgentConfig",
) -> int:
    """Execute file cleanup task.

    Deletes files older than the configured retention period from
    both temp and working directories.

    Args:
        file_service: FileService instance for cleanup operations.
        config: Application configuration with retention settings.

    Returns:
        Number of files deleted.

    Requirements:
        - 4.6: Clean up temporary files after retention period
        - 10.5: Delete files older than 24 hours from working folders
    """
    logger.info(
        "Starting file cleanup task (retention: %d hours)",
        config.file_retention_hours,
    )

    try:
        deleted_count = await file_service.cleanup_old_files(
            max_age_hours=config.file_retention_hours
        )
        logger.info(
            "File cleanup completed: deleted %d files",
            deleted_count,
        )
        return deleted_count
    except Exception as e:
        logger.error("File cleanup task failed: %s", e)
        raise
