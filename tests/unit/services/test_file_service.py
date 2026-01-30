"""Property-based tests for FileService.

Tests file validation, user isolation, and cleanup functionality
using hypothesis for property-based testing.

Properties tested:
- Property 4: File extension validation
- Property 5: File size limit enforcement
- Property 14: Filename sanitization
- Property 2: User-isolated file storage
- Property 17: Separate storage directories
- Property 9: File cleanup by age
- Property 16: Working folder cleared on new session
"""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from app.config import AgentConfig
from app.services.file_service import FileService, FileValidationResult


# ============================================================================
# Helper to create FileService instances
# ============================================================================


def create_file_service(temp_base: str, work_base: str) -> FileService:
    """Create a FileService with the given directories."""
    config = MagicMock(
        spec=AgentConfig,
        enable_file_attachments=True,
        max_file_size_mb=20,
        file_retention_hours=24,
        allowed_file_extensions=[
            ".txt", ".pdf", ".csv", ".json", ".xml", ".md",
            ".py", ".js", ".ts", ".html", ".css",
            ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ],
        temp_files_base_dir=temp_base,
        working_folder_base_dir=work_base,
    )
    return FileService(config)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as temp_base:
        with tempfile.TemporaryDirectory() as work_base:
            yield temp_base, work_base


@pytest.fixture
def file_service(temp_dirs):
    """Create FileService instance for testing."""
    temp_base, work_base = temp_dirs
    return create_file_service(temp_base, work_base)


# ============================================================================
# Strategies for property-based testing
# ============================================================================


# Valid file extensions from config
valid_extensions = st.sampled_from([
    ".txt", ".pdf", ".csv", ".json", ".xml", ".md",
    ".py", ".js", ".ts", ".html", ".css",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
])

# Invalid file extensions
invalid_extensions = st.sampled_from([
    ".exe", ".dll", ".bat", ".cmd", ".scr", ".msi",
    ".vbs", ".ps1", ".jar", ".app", ".dmg", ".iso",
])

# Safe filename characters
safe_filename_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
    min_size=1,
    max_size=50,
)

# User IDs (alphanumeric)
user_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=20,
)


# ============================================================================
# Property 4: File extension validation
# ============================================================================


class TestFileExtensionValidation:
    """Property 4: File extension validation.

    Requirements: 1.5, 9.1
    - Valid extensions should be accepted
    - Invalid extensions should be rejected
    """

    @given(
        filename_base=safe_filename_chars,
        extension=valid_extensions,
        file_size=st.integers(min_value=1, max_value=1024 * 1024),  # Up to 1MB
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_valid_extensions_accepted(
        self, file_service, filename_base, extension, file_size
    ):
        """Valid file extensions should pass validation."""
        filename = f"{filename_base}{extension}"

        result = file_service.validate_file(
            file_name=filename,
            file_size=file_size,
            mime_type=None,
        )

        assert result.valid is True
        assert result.error_message is None
        assert result.sanitized_name is not None

    @given(
        filename_base=safe_filename_chars,
        extension=invalid_extensions,
        file_size=st.integers(min_value=1, max_value=1024 * 1024),
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_invalid_extensions_rejected(
        self, file_service, filename_base, extension, file_size
    ):
        """Invalid file extensions should be rejected."""
        filename = f"{filename_base}{extension}"

        result = file_service.validate_file(
            file_name=filename,
            file_size=file_size,
            mime_type=None,
        )

        assert result.valid is False
        assert result.error_message is not None
        assert "not supported" in result.error_message.lower()


# ============================================================================
# Property 5: File size limit enforcement
# ============================================================================


class TestFileSizeLimitEnforcement:
    """Property 5: File size limit enforcement.

    Requirements: 1.6, 9.2
    - Files within size limit should be accepted
    - Files exceeding size limit should be rejected
    """

    @given(
        filename_base=safe_filename_chars,
        extension=valid_extensions,
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_files_within_limit_accepted(
        self, file_service, filename_base, extension
    ):
        """Files within size limit should pass validation."""
        filename = f"{filename_base}{extension}"
        max_bytes = file_service.config.max_file_size_mb * 1024 * 1024

        # Generate size within limit
        file_size = max_bytes - 1

        result = file_service.validate_file(
            file_name=filename,
            file_size=file_size,
            mime_type=None,
        )

        assert result.valid is True

    @given(
        filename_base=safe_filename_chars,
        extension=valid_extensions,
        excess_bytes=st.integers(min_value=1, max_value=10 * 1024 * 1024),
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_files_exceeding_limit_rejected(
        self, file_service, filename_base, extension, excess_bytes
    ):
        """Files exceeding size limit should be rejected."""
        filename = f"{filename_base}{extension}"
        max_bytes = file_service.config.max_file_size_mb * 1024 * 1024

        # Generate size exceeding limit
        file_size = max_bytes + excess_bytes

        result = file_service.validate_file(
            file_name=filename,
            file_size=file_size,
            mime_type=None,
        )

        assert result.valid is False
        assert result.error_message is not None
        assert "too large" in result.error_message.lower()


# ============================================================================
# Property 14: Filename sanitization
# ============================================================================


class TestFilenameSanitization:
    """Property 14: Filename sanitization.

    Requirements: 9.5, 9.6
    - Path traversal attempts should be removed
    - Special characters should be sanitized
    - Result should be safe for filesystem use
    """

    @given(filename_base=safe_filename_chars)
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_safe_filenames_preserved(self, file_service, filename_base):
        """Safe filenames should be mostly preserved."""
        result = file_service.sanitize_filename(filename_base)

        # Result should not be empty
        assert len(result) > 0
        # Result should not contain path separators
        assert "/" not in result
        assert "\\" not in result

    @given(
        prefix=st.sampled_from(["../", "..\\", "/", "\\", "./"]),
        filename_base=safe_filename_chars,
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_path_traversal_removed(self, file_service, prefix, filename_base):
        """Path traversal sequences should be removed."""
        malicious_name = f"{prefix}{filename_base}"

        result = file_service.sanitize_filename(malicious_name)

        # Result should not start with path traversal
        assert not result.startswith("../")
        assert not result.startswith("..\\")
        assert not result.startswith("/")
        assert not result.startswith("\\")
        # Result should not contain path traversal
        assert "../" not in result
        assert "..\\" not in result

    @given(
        filename_base=safe_filename_chars,
        special_chars=st.text(
            alphabet="!@#$%^&*()+=[]{}|;':\"<>,?`~",
            min_size=1,
            max_size=5,
        ),
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_special_characters_sanitized(
        self, file_service, filename_base, special_chars
    ):
        """Special characters should be sanitized."""
        dirty_name = f"{filename_base}{special_chars}"

        result = file_service.sanitize_filename(dirty_name)

        # Result should only contain safe characters
        for char in result:
            assert char.isalnum() or char in "._-"

    def test_null_bytes_removed(self, file_service):
        """Null bytes should be removed from filenames."""
        malicious_name = "test\x00file.txt"

        result = file_service.sanitize_filename(malicious_name)

        assert "\x00" not in result

    def test_empty_filename_handled(self, file_service):
        """Empty filenames should result in default name."""
        result = file_service.sanitize_filename("")

        assert len(result) > 0
        assert result == "unnamed_file"


# ============================================================================
# Property 2: User-isolated file storage
# ============================================================================


class TestUserIsolatedStorage:
    """Property 2: User-isolated file storage.

    Requirements: 1.2, 9.3
    - Each user should have their own temp directory
    - Each user should have their own working directory
    - Directories should be isolated from each other
    """

    @given(user_id=user_ids)
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_temp_dir_created_per_user(self, file_service, user_id):
        """Each user should get their own temp directory."""
        temp_dir = file_service.get_user_temp_dir(user_id)

        assert temp_dir.exists()
        assert temp_dir.is_dir()
        assert user_id in str(temp_dir)

    @given(user_id=user_ids)
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_working_dir_created_per_user(self, file_service, user_id):
        """Each user should get their own working directory."""
        work_dir = file_service.get_user_working_dir(user_id)

        assert work_dir.exists()
        assert work_dir.is_dir()
        assert user_id in str(work_dir)

    @given(
        user_id_1=user_ids,
        user_id_2=user_ids,
    )
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_user_directories_isolated(
        self, file_service, user_id_1, user_id_2
    ):
        """Different users should have different directories."""
        assume(user_id_1 != user_id_2)

        temp_dir_1 = file_service.get_user_temp_dir(user_id_1)
        temp_dir_2 = file_service.get_user_temp_dir(user_id_2)

        # Directories should be different paths
        assert temp_dir_1 != temp_dir_2

        # Each directory should contain its user_id in the path
        assert user_id_1 in str(temp_dir_1)
        assert user_id_2 in str(temp_dir_2)

        # Files in one directory should not be accessible from the other
        # (they are siblings, not parent-child)
        assert temp_dir_1.parent == temp_dir_2.parent


# ============================================================================
# Property 17: Separate storage directories
# ============================================================================


class TestSeparateStorageDirectories:
    """Property 17: Separate storage directories.

    Requirements: 10.6
    - Temp folder should be separate from working folder
    - Both should exist under different base paths
    """

    @given(user_id=user_ids)
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_temp_and_working_dirs_separate(self, file_service, user_id):
        """Temp and working directories should be separate."""
        temp_dir = file_service.get_user_temp_dir(user_id)
        work_dir = file_service.get_user_working_dir(user_id)

        # Directories should be different
        assert temp_dir != work_dir

        # Neither should be a subdirectory of the other
        assert not str(temp_dir).startswith(str(work_dir))
        assert not str(work_dir).startswith(str(temp_dir))


# ============================================================================
# Property 9: File cleanup by age
# ============================================================================


class TestFileCleanupByAge:
    """Property 9: File cleanup by age.

    Requirements: 4.6, 10.5
    - Files older than retention period should be deleted
    - Recent files should be preserved
    """

    @pytest.mark.asyncio
    async def test_old_files_deleted(self, file_service):
        """Files older than max age should be deleted."""
        user_id = "testuser"
        temp_dir = file_service.get_user_temp_dir(user_id)

        # Create a test file
        test_file = temp_dir / "old_file.txt"
        test_file.write_text("test content")

        # Set modification time to 2 hours ago
        old_time = time.time() - (2 * 3600)
        os.utime(test_file, (old_time, old_time))

        # Run cleanup with 1 hour max age
        deleted_count = await file_service.cleanup_old_files(max_age_hours=1)

        assert deleted_count >= 1
        assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_recent_files_preserved(self, file_service):
        """Files newer than max age should be preserved."""
        user_id = "testuser"
        temp_dir = file_service.get_user_temp_dir(user_id)

        # Create a recent test file
        test_file = temp_dir / "recent_file.txt"
        test_file.write_text("test content")

        # Run cleanup with 24 hour max age (file is brand new)
        deleted_count = await file_service.cleanup_old_files(max_age_hours=24)

        # File should still exist
        assert test_file.exists()


# ============================================================================
# Property 16: Working folder cleared on new session
# ============================================================================


class TestWorkingFolderCleared:
    """Property 16: Working folder cleared on new session.

    Requirements: 10.4
    - All files in working folder should be deleted on clear
    - Directory structure should remain intact
    """

    @given(user_id=user_ids)
    @settings(
        max_examples=10,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_working_folder_cleared(self, file_service, user_id):
        """Working folder should be cleared of all files."""
        work_dir = file_service.get_user_working_dir(user_id)

        # Create some test files
        (work_dir / "file1.txt").write_text("content1")
        (work_dir / "file2.txt").write_text("content2")

        # Clear the working folder
        file_service.clear_working_folder(user_id)

        # Directory should exist but be empty
        assert work_dir.exists()
        assert list(work_dir.iterdir()) == []

    @given(user_id=user_ids)
    @settings(
        max_examples=10,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_clear_nonexistent_folder_safe(self, file_service, user_id):
        """Clearing a non-existent folder should not raise errors."""
        # Use a user ID that hasn't been used
        unique_user = f"nonexistent_{user_id}"

        # This should not raise an exception
        file_service.clear_working_folder(unique_user)


# ============================================================================
# Edge case tests
# ============================================================================


class TestEdgeCases:
    """Edge case tests for file service."""

    def test_validate_file_with_path_traversal_in_name(self, file_service):
        """Files with path traversal in name should be rejected."""
        result = file_service.validate_file(
            file_name="../../../etc/passwd",
            file_size=100,
            mime_type=None,
        )

        assert result.valid is False
        assert "path traversal" in result.error_message.lower()

    def test_validate_file_starting_with_slash(self, file_service):
        """Files starting with slash should be rejected."""
        result = file_service.validate_file(
            file_name="/etc/passwd",
            file_size=100,
            mime_type=None,
        )

        assert result.valid is False

    def test_is_image_file_detection(self, file_service):
        """Image files should be correctly detected."""
        assert file_service.is_image_file("photo.jpg") is True
        assert file_service.is_image_file("photo.jpeg") is True
        assert file_service.is_image_file("photo.png") is True
        assert file_service.is_image_file("photo.gif") is True
        assert file_service.is_image_file("photo.webp") is True
        assert file_service.is_image_file("document.pdf") is False
        assert file_service.is_image_file("script.py") is False
