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
        _logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Never break the application because of logging.
        _logger.info('{"event":"%s","error":"failed_to_serialize"}', event)
