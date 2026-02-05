"""Error log file handler for capturing errors and warnings to a file.

This module provides file-based error logging for tool execution errors,
warnings, and other issues that need to be tracked for debugging purposes.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import AgentConfig


_error_file_handler: RotatingFileHandler | None = None


def setup_error_log_file(config: "AgentConfig") -> RotatingFileHandler | None:
    """Setup error log file handler based on configuration.

    Args:
        config: Application configuration with error log settings.

    Returns:
        The configured RotatingFileHandler, or None if disabled.
    """
    global _error_file_handler

    if not getattr(config, "error_log_file_enabled", True):
        return None

    # Get configuration values
    log_path = getattr(config, "error_log_file_path", "./logs/errors.log")
    log_level_str = getattr(config, "error_log_level", "WARNING").upper()
    max_bytes = getattr(config, "error_log_max_bytes", 10_485_760)
    backup_count = getattr(config, "error_log_backup_count", 5)

    # Map string log level to logging constant
    log_level = getattr(logging, log_level_str, logging.WARNING)

    # Resolve the log file path
    log_file = Path(log_path).expanduser()
    if not log_file.is_absolute():
        # Resolve relative to repo root
        try:
            from app.config import _find_repo_root
            repo_root = _find_repo_root(start=Path(__file__))
            log_file = repo_root / log_file
        except Exception:
            log_file = Path.cwd() / log_file

    log_file = log_file.resolve()

    # Ensure the log directory exists
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        # Log to stderr if we can't create the directory
        print(f"Warning: Cannot create error log directory {log_file.parent}: {e}", file=sys.stderr)
        return None

    # Create the rotating file handler
    try:
        _error_file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        _error_file_handler.setLevel(log_level)

        # Use a detailed format for error logs
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _error_file_handler.setFormatter(formatter)

        # Add the handler to the root logger to capture all errors
        root_logger = logging.getLogger()
        root_logger.addHandler(_error_file_handler)

        # Also add to specific loggers that emit important errors
        for logger_name in [
            "mordecai.trace",
            "app",
            "app.tools",
            "app.services",
            "app.sqs",
            "app.telegram",
        ]:
            logger = logging.getLogger(logger_name)
            # Avoid duplicating if parent already has the handler
            if _error_file_handler not in logger.handlers:
                logger.addHandler(_error_file_handler)

        logging.getLogger(__name__).info(
            "Error log file handler initialized: %s (level=%s)", log_file, log_level_str
        )
        return _error_file_handler

    except (OSError, PermissionError) as e:
        print(f"Warning: Cannot create error log file {log_file}: {e}", file=sys.stderr)
        return None


def get_error_log_handler() -> RotatingFileHandler | None:
    """Get the current error log file handler.

    Returns:
        The error log file handler if configured, or None.
    """
    return _error_file_handler


def log_tool_error(
    tool_name: str,
    error: Exception | str,
    *,
    user_id: str | None = None,
    trace_id: str | None = None,
    extra: dict | None = None,
) -> None:
    """Log a tool execution error to the error log file.

    Args:
        tool_name: Name of the tool that encountered the error.
        error: The exception or error message.
        user_id: Optional user ID for context.
        trace_id: Optional trace ID for correlation.
        extra: Additional context to include in the log.
    """
    logger = logging.getLogger(f"app.tools.{tool_name}")

    error_msg = str(error)
    error_type = type(error).__name__ if isinstance(error, Exception) else "Error"

    context_parts = [f"tool={tool_name}", f"error_type={error_type}"]
    if user_id:
        context_parts.append(f"user_id={user_id}")
    if trace_id:
        context_parts.append(f"trace_id={trace_id}")
    if extra:
        for k, v in extra.items():
            context_parts.append(f"{k}={v}")

    context_str = " ".join(context_parts)
    logger.error("[%s] %s", context_str, error_msg)


def log_tool_warning(
    tool_name: str,
    message: str,
    *,
    user_id: str | None = None,
    trace_id: str | None = None,
    extra: dict | None = None,
) -> None:
    """Log a tool execution warning to the error log file.

    Args:
        tool_name: Name of the tool that encountered the warning.
        message: The warning message.
        user_id: Optional user ID for context.
        trace_id: Optional trace ID for correlation.
        extra: Additional context to include in the log.
    """
    logger = logging.getLogger(f"app.tools.{tool_name}")

    context_parts = [f"tool={tool_name}"]
    if user_id:
        context_parts.append(f"user_id={user_id}")
    if trace_id:
        context_parts.append(f"trace_id={trace_id}")
    if extra:
        for k, v in extra.items():
            context_parts.append(f"{k}={v}")

    context_str = " ".join(context_parts)
    logger.warning("[%s] %s", context_str, message)
