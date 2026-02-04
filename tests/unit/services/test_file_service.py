"""Unit tests for FileService (deterministic).

Historically this module contained Hypothesis/property-based tests.
Those have been replaced with small deterministic tests to keep the
suite lightweight and dependency-free.
"""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
            ".txt",
            ".pdf",
            ".csv",
            ".json",
            ".xml",
            ".md",
            ".py",
            ".js",
            ".ts",
            ".html",
            ".css",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
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


VALID_EXTENSIONS = [
    ".txt",
    ".pdf",
    ".csv",
    ".json",
    ".xml",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
]

INVALID_EXTENSIONS = [
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".scr",
    ".msi",
    ".vbs",
    ".ps1",
    ".jar",
    ".app",
    ".dmg",
    ".iso",
]

USER_IDS = ["alice", "bob", "user123", "test-user"]

SAFE_FILENAME_BASES = [
    "hello_world",
    "Report_2026-01-31",
    "abcDEF123",
]


@pytest.mark.parametrize("extension", VALID_EXTENSIONS)
def test_valid_extensions_accepted(file_service: FileService, extension: str):
    filename = f"hello{extension}"
    result = file_service.validate_file(
        file_name=filename,
        file_size=1024,
        mime_type=None,
    )
    assert result.valid is True
    assert result.error_message is None
    assert result.sanitized_name


@pytest.mark.parametrize("extension", INVALID_EXTENSIONS)
def test_invalid_extensions_rejected(file_service: FileService, extension: str):
    filename = f"hello{extension}"
    result = file_service.validate_file(
        file_name=filename,
        file_size=1024,
        mime_type=None,
    )
    assert result.valid is False
    assert result.error_message
    assert "not supported" in result.error_message.lower()


def test_files_within_size_limit_accepted(file_service: FileService):
    max_bytes = file_service.config.max_file_size_mb * 1024 * 1024
    filename = "within_limit.txt"
    result = file_service.validate_file(
        file_name=filename,
        file_size=max_bytes - 1,
        mime_type=None,
    )
    assert result.valid is True


@pytest.mark.parametrize("excess_bytes", [1, 1024, 1024 * 1024])
def test_files_exceeding_size_limit_rejected(file_service: FileService, excess_bytes: int):
    max_bytes = file_service.config.max_file_size_mb * 1024 * 1024
    filename = "too_large.txt"
    result = file_service.validate_file(
        file_name=filename,
        file_size=max_bytes + excess_bytes,
        mime_type=None,
    )
    assert result.valid is False
    assert result.error_message
    assert "too large" in result.error_message.lower()


# ============================================================================
# Property 14: Filename sanitization
# ============================================================================


@pytest.mark.parametrize("filename_base", SAFE_FILENAME_BASES)
def test_safe_filenames_preserved(file_service: FileService, filename_base: str):
    result = file_service.sanitize_filename(filename_base)
    assert result
    assert "/" not in result
    assert "\\" not in result


@pytest.mark.parametrize("prefix", ["../", "..\\", "/", "\\", "./"])
def test_path_traversal_removed(file_service: FileService, prefix: str):
    malicious_name = f"{prefix}hello_world"
    result = file_service.sanitize_filename(malicious_name)
    assert not result.startswith("../")
    assert not result.startswith("..\\")
    assert not result.startswith("/")
    assert not result.startswith("\\")
    assert "../" not in result
    assert "..\\" not in result


@pytest.mark.parametrize("dirty_suffix", ["!", "@@@", "[]{}", "<>,"])
def test_special_characters_sanitized(file_service: FileService, dirty_suffix: str):
    dirty_name = f"safe_name{dirty_suffix}"
    result = file_service.sanitize_filename(dirty_name)
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


@pytest.mark.parametrize("user_id", USER_IDS)
def test_temp_dir_created_per_user(file_service: FileService, user_id: str):
    temp_dir = file_service.get_user_temp_dir(user_id)
    assert temp_dir.exists()
    assert temp_dir.is_dir()
    assert user_id in str(temp_dir)


@pytest.mark.parametrize("user_id", USER_IDS)
def test_working_dir_created_per_user(file_service: FileService, user_id: str):
    work_dir = file_service.get_user_working_dir(user_id)
    assert work_dir.exists()
    assert work_dir.is_dir()
    assert user_id in str(work_dir)


def test_user_directories_isolated(file_service: FileService):
    user_id_1 = "alice"
    user_id_2 = "bob"
    assert user_id_1 != user_id_2

    temp_dir_1 = file_service.get_user_temp_dir(user_id_1)
    temp_dir_2 = file_service.get_user_temp_dir(user_id_2)
    assert temp_dir_1 != temp_dir_2
    assert temp_dir_1.parent == temp_dir_2.parent


# ============================================================================
# Property 17: Separate storage directories
# ============================================================================


def test_temp_and_working_dirs_separate(file_service: FileService):
    user_id = "alice"
    temp_dir = file_service.get_user_temp_dir(user_id)
    work_dir = file_service.get_user_working_dir(user_id)
    assert temp_dir != work_dir
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

    @pytest.mark.asyncio
    async def test_nested_dirs_deleted_when_stale(self, file_service: FileService):
        """Nested directories under temp/workspace are deleted when the newest file is stale.

        This matches the intended behavior for workspace artifacts: if a user's
        working folder hasn't changed for > retention, delete the whole tree.
        """

        user_id = "testuser"
        temp_dir = file_service.get_user_temp_dir(user_id)
        work_dir = file_service.get_user_working_dir(user_id)

        # Create nested content under both bases
        nested_temp = temp_dir / "a" / "b"
        nested_work = work_dir / "x" / "y"
        nested_temp.mkdir(parents=True, exist_ok=True)
        nested_work.mkdir(parents=True, exist_ok=True)

        old_temp_file = nested_temp / "old.txt"
        old_work_file = nested_work / "old.txt"
        old_temp_file.write_text("temp")
        old_work_file.write_text("work")

        # Make the files old enough to be deleted
        old_time = time.time() - (2 * 3600)
        os.utime(old_temp_file, (old_time, old_time))
        os.utime(old_work_file, (old_time, old_time))

        deleted_count = await file_service.cleanup_old_files(max_age_hours=1)
        assert deleted_count >= 2

        # Entire per-user dirs should be gone (deleted as a unit)
        assert not temp_dir.exists()
        assert not work_dir.exists()


# ============================================================================
# Property 16: Working folder cleared on new session
# ============================================================================


@pytest.mark.parametrize("user_id", ["alice", "bob"])
def test_working_folder_cleared(file_service: FileService, user_id: str):
    work_dir = file_service.get_user_working_dir(user_id)
    (work_dir / "file1.txt").write_text("content1")
    (work_dir / "file2.txt").write_text("content2")

    file_service.clear_working_folder(user_id)

    assert work_dir.exists()
    assert list(work_dir.iterdir()) == []


def test_clear_nonexistent_folder_safe(file_service: FileService):
    file_service.clear_working_folder("nonexistent_user")


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
