"""File service for file download, storage, and management.

This module provides file handling capabilities for the Telegram bot,
including file validation, download, storage, and cleanup.

Requirements:
- 1.1: Download files from Telegram servers
- 1.2: Store files in user-specific directories
- 1.5: Validate file extensions against allowlist
- 1.6: Reject files exceeding maximum size
- 9.1: Validate file extensions before processing
- 9.2: Scan filenames for path traversal attempts
- 9.5: Sanitize filenames before storing
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Bot

from app.config import AgentConfig

logger = logging.getLogger(__name__)


@dataclass
class FileMetadata:
    """Metadata for a downloaded file.

    Attributes:
        file_id: Telegram file ID or generated UUID.
        file_name: Original or sanitized filename.
        file_path: Full path to local file.
        mime_type: MIME type if known.
        file_size: Size in bytes.
        is_image: Whether file is an image.
        downloaded_at: Timestamp of download.
    """

    file_id: str
    file_name: str
    file_path: str
    mime_type: str | None
    file_size: int
    is_image: bool
    downloaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FileValidationResult:
    """Result of file validation.

    Attributes:
        valid: Whether the file passed validation.
        error_message: Error message if validation failed.
        sanitized_name: Sanitized filename if validation passed.
    """

    valid: bool
    error_message: str | None = None
    sanitized_name: str | None = None


class FileService:
    """Service for file download, storage, and management.

    Handles file validation, download from Telegram, storage in
    user-specific directories, and cleanup of old files.

    Requirements:
        - 1.1: Download files from Telegram servers
        - 1.2: Store files in user-specific directories
        - 9.3: Store files in isolated user-specific directories
    """

    # Image extensions for detection
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the file service.

        Args:
            config: Application configuration with file settings.
        """
        self.config = config
        self._temp_base = Path(config.temp_files_base_dir)
        self._work_base = Path(config.working_folder_base_dir)
        self._temp_base.mkdir(parents=True, exist_ok=True)
        self._work_base.mkdir(parents=True, exist_ok=True)

    def validate_file(
        self,
        file_name: str,
        file_size: int,
        mime_type: str | None,
    ) -> FileValidationResult:
        """Validate file before download.

        Checks:
        - File size within limits
        - Extension in allowlist
        - No path traversal in filename

        Args:
            file_name: Original filename.
            file_size: File size in bytes.
            mime_type: MIME type if known.

        Returns:
            FileValidationResult with validation status.

        Requirements:
            - 1.5: Validate file extensions against allowlist
            - 1.6: Reject files exceeding maximum size
            - 9.1: Validate file extensions before processing
            - 9.2: Scan filenames for path traversal attempts
        """
        # Check for path traversal
        if ".." in file_name or file_name.startswith(("/", "\\")):
            return FileValidationResult(
                valid=False,
                error_message="Invalid filename: path traversal detected",
            )

        # Check file size
        max_bytes = self.config.max_file_size_mb * 1024 * 1024
        if file_size > max_bytes:
            return FileValidationResult(
                valid=False,
                error_message=(
                    f"File too large. Maximum size is "
                    f"{self.config.max_file_size_mb}MB"
                ),
            )

        # Check extension
        ext = Path(file_name).suffix.lower()
        if ext not in self.config.allowed_file_extensions:
            allowed = ", ".join(sorted(self.config.allowed_file_extensions))
            return FileValidationResult(
                valid=False,
                error_message=(
                    f"File type '{ext}' not supported. "
                    f"Allowed types: {allowed}"
                ),
            )

        # Sanitize filename
        sanitized = self.sanitize_filename(file_name)

        return FileValidationResult(
            valid=True,
            sanitized_name=sanitized,
        )

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to prevent filesystem issues.

        Removes path traversal sequences, null bytes, and invalid
        characters. Preserves the file extension.

        Args:
            filename: Original filename.

        Returns:
            Sanitized filename safe for filesystem use.

        Requirements:
            - 9.5: Sanitize filenames before storing
        """
        # Remove path components
        filename = Path(filename).name

        # Remove null bytes
        filename = filename.replace("\x00", "")

        # Remove path traversal sequences
        filename = filename.replace("../", "").replace("..\\", "")

        # Remove leading dots (hidden files) and slashes
        filename = filename.lstrip("./\\")

        # Replace invalid characters with underscore
        # Keep alphanumeric, dots, hyphens, underscores
        filename = re.sub(r"[^\w.\-]", "_", filename)

        # Ensure filename is not empty
        if not filename or filename == ".":
            filename = "unnamed_file"

        return filename

    async def download_file(
        self,
        bot: "Bot",
        file_id: str,
        user_id: str,
        file_name: str,
        mime_type: str | None,
    ) -> FileMetadata:
        """Download file from Telegram and store locally.

        Args:
            bot: Telegram bot instance.
            file_id: Telegram file ID.
            user_id: User ID for directory isolation.
            file_name: Sanitized filename.
            mime_type: MIME type if known.

        Returns:
            FileMetadata with download information.

        Requirements:
            - 1.1: Download files from Telegram servers
            - 1.2: Store files in user-specific directories
        """
        # Get user's temp directory
        user_dir = self.get_user_temp_dir(user_id)

        # Get file from Telegram
        tg_file = await bot.get_file(file_id)

        # Build local path
        local_path = user_dir / file_name

        # Download to local path
        await tg_file.download_to_drive(local_path)

        # Get actual file size
        file_size = local_path.stat().st_size

        # Determine if image
        is_image = self.is_image_file(local_path)

        logger.info(
            "Downloaded file %s for user %s: %s (%d bytes)",
            file_id,
            user_id,
            local_path,
            file_size,
        )

        return FileMetadata(
            file_id=file_id,
            file_name=file_name,
            file_path=str(local_path),
            mime_type=mime_type,
            file_size=file_size,
            is_image=is_image,
        )

    def get_user_temp_dir(self, user_id: str) -> Path:
        """Get temporary directory for user's downloaded files.

        Creates the directory if it doesn't exist.

        Args:
            user_id: User ID for directory isolation.

        Returns:
            Path to user's temp directory.

        Requirements:
            - 1.2: Store files in user-specific directories
            - 9.3: Store files in isolated user-specific directories
        """
        user_dir = self._temp_base / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def get_user_working_dir(self, user_id: str) -> Path:
        """Get working directory for user's agent operations.

        Creates the directory if it doesn't exist.

        Args:
            user_id: User ID for directory isolation.

        Returns:
            Path to user's working directory.

        Requirements:
            - 10.1: Create user-specific working folder
            - 10.6: Working folder separate from temp folder
        """
        user_dir = self._work_base / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def clear_working_folder(self, user_id: str) -> None:
        """Clear all files from user's working folder.

        Args:
            user_id: User ID for directory isolation.

        Requirements:
            - 10.4: Clear working folder on new session
        """
        user_dir = self._work_base / user_id
        if user_dir.exists():
            for item in user_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    import shutil
                    shutil.rmtree(item)
            logger.info("Cleared working folder for user %s", user_id)

    def is_image_file(self, file_path: str | Path) -> bool:
        """Check if file is an image based on extension.

        Args:
            file_path: Path to the file.

        Returns:
            True if file has an image extension.
        """
        ext = Path(file_path).suffix.lower()
        return ext in self.IMAGE_EXTENSIONS

    async def cleanup_old_files(self, max_age_hours: int) -> int:
        """Delete files older than max_age_hours.

        Args:
            max_age_hours: Maximum age in hours before deletion.

        Returns:
            Count of deleted files.

        Requirements:
            - 4.6: Clean up temporary files after retention period
            - 10.5: Delete files older than 24 hours from working folders
        """
        import time

        deleted_count = 0
        max_age_seconds = max_age_hours * 3600
        current_time = time.time()

        # Clean temp directories
        for user_dir in self._temp_base.iterdir():
            if user_dir.is_dir():
                for file_path in user_dir.iterdir():
                    if file_path.is_file():
                        age = current_time - file_path.stat().st_mtime
                        if age > max_age_seconds:
                            file_path.unlink()
                            deleted_count += 1

        # Clean working directories
        for user_dir in self._work_base.iterdir():
            if user_dir.is_dir():
                for file_path in user_dir.iterdir():
                    if file_path.is_file():
                        age = current_time - file_path.stat().st_mtime
                        if age > max_age_seconds:
                            file_path.unlink()
                            deleted_count += 1

        logger.info(
            "Cleanup: deleted %d files older than %d hours",
            deleted_count,
            max_age_hours,
        )

        return deleted_count
