"""Structured trace/event logging.

We emit a single JSON object per line so logs are easy to grep and ship.

We do *not* log chain-of-thought; events should describe observable actions:
- message received
- tool called
- tool returned
- errors
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.observability.redaction import sanitize
from app.observability.trace_context import get_actor_id, get_trace_id


_logger = logging.getLogger("mordecai.trace")
_error_logger = logging.getLogger("app.tools.trace")


def trace_event(
    event: str,
    *,
    max_chars: int = 2000,
    **fields: Any,
) -> None:
    """Emit a structured trace event.

    Args:
        event: Short event name, e.g. 'agent.message.start'.
        max_chars: Max chars for any string field after sanitization.
        **fields: Event payload (will be sanitized).
    """

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "trace_id": get_trace_id(),
        "actor_id": get_actor_id(),
    }

    # Sanitize payload fields.
    for k, v in fields.items():
        record[k] = sanitize(v, max_chars=max_chars)

    try:
        json_str = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        _logger.info(json_str)

        # Also log errors to the error logger for file-based error tracking
        if ".error" in event or "error" in fields:
            error_msg = fields.get("error", "Unknown error")
            error_type = fields.get("error_type", "Error")
            _error_logger.error(
                "[%s] %s: %s (trace_id=%s, actor_id=%s)",
                event,
                error_type,
                error_msg,
                record.get("trace_id"),
                record.get("actor_id"),
            )
    except Exception:
        # Never break the application because of logging.
        _logger.info('{"event":"%s","error":"failed_to_serialize"}', event)
