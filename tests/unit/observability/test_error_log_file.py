"""Tests for error log file handler."""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.observability.error_log_file import (
    get_error_log_handler,
    log_tool_error,
    log_tool_warning,
    setup_error_log_file,
)


@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for log files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config(temp_log_dir):
    """Create a mock config with error log settings."""
    config = MagicMock()
    config.error_log_file_enabled = True
    config.error_log_file_path = str(temp_log_dir / "errors.log")
    config.error_log_level = "WARNING"
    config.error_log_max_bytes = 1024 * 1024  # 1 MB
    config.error_log_backup_count = 3
    return config


@pytest.fixture(autouse=True)
def cleanup_handlers():
    """Clean up handlers after each test to avoid interference."""
    yield
    # Remove any handlers we added during tests
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if hasattr(handler, 'baseFilename') and 'errors.log' in str(handler.baseFilename):
            root_logger.removeHandler(handler)
            handler.close()


class TestSetupErrorLogFile:
    """Tests for setup_error_log_file function."""

    def test_creates_log_file_and_directory(self, mock_config, temp_log_dir):
        """Test that setup creates the log directory and initializes handler."""
        handler = setup_error_log_file(mock_config)

        assert handler is not None
        assert (temp_log_dir / "errors.log").parent.exists()

    def test_returns_none_when_disabled(self, mock_config):
        """Test that setup returns None when error logging is disabled."""
        mock_config.error_log_file_enabled = False

        handler = setup_error_log_file(mock_config)

        assert handler is None

    def test_sets_correct_log_level(self, mock_config):
        """Test that handler is configured with correct log level."""
        mock_config.error_log_level = "ERROR"

        handler = setup_error_log_file(mock_config)

        assert handler is not None
        assert handler.level == logging.ERROR

    def test_sets_warning_level_by_default(self, mock_config):
        """Test that WARNING level captures both warnings and errors."""
        handler = setup_error_log_file(mock_config)

        assert handler is not None
        assert handler.level == logging.WARNING

    def test_handler_has_correct_formatter(self, mock_config):
        """Test that handler has detailed format with timestamp and location."""
        handler = setup_error_log_file(mock_config)

        assert handler is not None
        assert handler.formatter is not None
        # Check format includes key fields
        format_str = handler.formatter._fmt
        assert "%(asctime)s" in format_str
        assert "%(name)s" in format_str
        assert "%(levelname)s" in format_str
        assert "%(filename)s" in format_str
        assert "%(lineno)d" in format_str


class TestLogToolError:
    """Tests for log_tool_error helper function."""

    def test_logs_error_with_tool_name(self, caplog, mock_config):
        """Test that errors include tool name in context."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.ERROR)

        log_tool_error("shell", "Command failed")

        assert any("tool=shell" in r.message for r in caplog.records)

    def test_logs_error_type_for_exceptions(self, caplog, mock_config):
        """Test that exception type is captured."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.ERROR)

        try:
            raise ValueError("test error")
        except ValueError as e:
            log_tool_error("file_read", e)

        assert any("error_type=ValueError" in r.message for r in caplog.records)

    def test_logs_user_id_when_provided(self, caplog, mock_config):
        """Test that user_id is included when provided."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.ERROR)

        log_tool_error("shell", "Error", user_id="user-123")

        assert any("user_id=user-123" in r.message for r in caplog.records)

    def test_logs_trace_id_when_provided(self, caplog, mock_config):
        """Test that trace_id is included when provided."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.ERROR)

        log_tool_error("shell", "Error", trace_id="trace-abc")

        assert any("trace_id=trace-abc" in r.message for r in caplog.records)

    def test_logs_extra_context(self, caplog, mock_config):
        """Test that extra context dict is included."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.ERROR)

        log_tool_error("shell", "Error", extra={"command": "ls", "exit_code": 1})

        records = [r.message for r in caplog.records if "tool=shell" in r.message]
        assert records
        assert "command=ls" in records[0]
        assert "exit_code=1" in records[0]


class TestLogToolWarning:
    """Tests for log_tool_warning helper function."""

    def test_logs_warning_with_tool_name(self, caplog, mock_config):
        """Test that warnings include tool name in context."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.WARNING)

        log_tool_warning("file_write", "File already exists")

        assert any("tool=file_write" in r.message for r in caplog.records)
        assert any(r.levelno == logging.WARNING for r in caplog.records if "file_write" in r.message)

    def test_logs_user_id_when_provided(self, caplog, mock_config):
        """Test that user_id is included when provided."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.WARNING)

        log_tool_warning("shell", "Timeout approaching", user_id="user-456")

        assert any("user_id=user-456" in r.message for r in caplog.records)


class TestErrorLogFileWriting:
    """Tests for actual file writing behavior."""

    def test_writes_errors_to_file(self, mock_config, temp_log_dir):
        """Test that errors are written to the log file."""
        handler = setup_error_log_file(mock_config)
        assert handler is not None

        log_tool_error("shell", "Test error message")
        handler.flush()

        log_file = temp_log_dir / "errors.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "Test error message" in content
        assert "tool=shell" in content

    def test_writes_warnings_to_file(self, mock_config, temp_log_dir):
        """Test that warnings are written to the log file."""
        handler = setup_error_log_file(mock_config)
        assert handler is not None

        log_tool_warning("file_read", "File not found warning")
        handler.flush()

        log_file = temp_log_dir / "errors.log"
        content = log_file.read_text()
        assert "File not found warning" in content

    def test_does_not_write_info_level_logs(self, mock_config, temp_log_dir):
        """Test that INFO level logs are not written when level is WARNING."""
        handler = setup_error_log_file(mock_config)
        assert handler is not None

        # Log an info message directly
        logger = logging.getLogger("app.tools.test")
        logger.info("This is an info message")
        handler.flush()

        log_file = temp_log_dir / "errors.log"
        if log_file.exists():
            content = log_file.read_text()
            assert "This is an info message" not in content


class TestGetErrorLogHandler:
    """Tests for get_error_log_handler function."""

    def test_returns_handler_after_setup(self, mock_config):
        """Test that get_error_log_handler returns the configured handler."""
        setup_error_log_file(mock_config)

        handler = get_error_log_handler()

        assert handler is not None

    def test_returns_none_before_setup(self):
        """Test that get_error_log_handler returns None before setup."""
        # Reset the global handler
        import app.observability.error_log_file as module
        module._error_file_handler = None

        handler = get_error_log_handler()

        assert handler is None


class TestTraceLoggingErrorIntegration:
    """Tests for integration with trace_logging module."""

    def test_trace_event_errors_logged_to_error_file(self, mock_config, temp_log_dir, caplog):
        """Test that trace events with errors are logged to error file."""
        setup_error_log_file(mock_config)
        caplog.set_level(logging.ERROR)

        from app.observability.trace_logging import trace_event

        trace_event("tool.shell.error", error="Command failed", error_type="TimeoutError")

        # Check that error was logged
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("tool.shell.error" in r.message for r in error_records)
