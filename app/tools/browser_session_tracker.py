"""Per-message browser session tracking.

We need to know which AgentCore browser sessions were used in the current
message so we can surface replay links after the agent finishes.

Design notes:
- Tool execution happens in a background thread.
- The send_progress callback reliably propagates into that context.
- Therefore we key per-message tracking off the identity of the progress callback.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar


_progress_callback_key: ContextVar[int | None] = ContextVar(
    "browser_session_tracker_key", default=None
)

_sessions_lock = threading.Lock()
_sessions_by_key: dict[int, list[str]] = {}
_used_flag_by_key: dict[int, bool] = {}


def set_progress_callback_key(callback) -> None:
    """Bind tracker state to the current message via the progress callback identity."""

    if callback is None:
        _progress_callback_key.set(None)
        return

    key = id(callback)
    _progress_callback_key.set(key)

    with _sessions_lock:
        _sessions_by_key[key] = []
        _used_flag_by_key[key] = False


def clear_progress_callback_key() -> None:
    key = _progress_callback_key.get()
    if key is not None:
        with _sessions_lock:
            _sessions_by_key.pop(key, None)
            _used_flag_by_key.pop(key, None)
    _progress_callback_key.set(None)


def mark_browser_used() -> None:
    key = _progress_callback_key.get()
    if key is None:
        return
    with _sessions_lock:
        _used_flag_by_key[key] = True


def register_session_id(session_id: str) -> None:
    session_id = (session_id or "").strip()
    if not session_id:
        return

    key = _progress_callback_key.get()
    if key is None:
        return

    with _sessions_lock:
        existing = _sessions_by_key.get(key)
        if existing is None:
            existing = []
            _sessions_by_key[key] = existing
        # Keep insertion order, avoid duplicates.
        if session_id not in existing:
            existing.append(session_id)


def get_and_clear() -> tuple[bool, list[str]]:
    """Return (used_browser_tool, session_ids) and clear state for this message."""

    key = _progress_callback_key.get()
    if key is None:
        return False, []

    with _sessions_lock:
        used = bool(_used_flag_by_key.get(key, False))
        sessions = list(_sessions_by_key.get(key, []))
        _used_flag_by_key[key] = False
        _sessions_by_key[key] = []

    return used, sessions
