"""Logging helpers and filters.

This module centralizes small logging tweaks so they can be applied from
multiple entrypoints (e.g. `python -m app.main` and `uvicorn app.asgi:app`).
"""

from __future__ import annotations

import logging
from typing import Any


class SuppressHealthCheckAccessLog(logging.Filter):
    """Drop Uvicorn access log records for the health check endpoint.

    This prevents noisy lines like:
        INFO: 127.0.0.1:36130 - "GET /health HTTP/1.1" 200 OK

    while keeping access logs for all other routes.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 (filter)
        # Uvicorn's access logger uses %-formatting with args similar to:
        #   (client_addr, method, full_path, http_version, status_code)
        # but we also fall back to message parsing for safety.
        try:
            args: Any = record.args
            if isinstance(args, tuple) and len(args) >= 3:
                path = str(args[2])
                if path == "/health" or path.startswith("/health?"):
                    return False

            message = record.getMessage()
            # Defensive fallback: if formatting changes, still suppress health logs.
            if '"GET /health ' in message or '"HEAD /health ' in message:
                return False
        except Exception:
            # Never break logging.
            return True

        return True


def install_uvicorn_access_log_filters() -> None:
    """Install filters for Uvicorn loggers.

    Safe to call multiple times.
    """

    access_logger = logging.getLogger("uvicorn.access")

    # Avoid duplicating the filter if called repeatedly.
    for existing in access_logger.filters:
        if isinstance(existing, SuppressHealthCheckAccessLog):
            return

    access_logger.addFilter(SuppressHealthCheckAccessLog())
