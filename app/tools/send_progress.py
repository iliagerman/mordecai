"""Tool for sending progress updates to the user via Telegram.

This tool allows the agent to send short status updates during long-running
operations, keeping the user informed about progress.

Concurrency note:
The agent can process multiple messages concurrently (across users and/or
prefetched messages). Tool state must therefore be *per message/task*.

We use :mod:`contextvars` so callbacks are isolated to the current asyncio task
(and propagate into the background thread used for agent invocation via
:func:`asyncio.to_thread`).
"""

import logging
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

logger = logging.getLogger(__name__)

# Per-task callback reference - set by message processor before agent runs
_progress_callback: ContextVar[Callable[[str], Awaitable[bool]] | None] = ContextVar(
    "progress_callback", default=None
)


def set_progress_callback(callback: Callable[[str], Awaitable[bool]]) -> None:
    """Set the callback for sending progress updates.

    Called by message processor before creating the agent.

    Args:
        callback: Async callback to send a progress message. Takes a message
            string and returns bool for success.
    """
    _progress_callback.set(callback)


def clear_progress_callback() -> None:
    """Clear the progress callback after processing."""
    _progress_callback.set(None)


@tool(
    name="send_progress",
    description=(
        "Send a short progress update to the user. Use this during long-running "
        "operations to keep the user informed. Examples: 'Reading file...', "
        "'Running analysis...', 'Processing data...', 'Almost done...'. "
        "Keep updates brief (under 100 characters recommended). "
        "Use present continuous tense (e.g., 'Processing...' not 'Processed'). "
        "Only use for operations that take more than a few seconds."
    ),
)
def send_progress(message: str) -> str:
    """Send a progress update to the user via Telegram.

    Args:
        message: Short progress message (under 100 characters recommended).
            Use present continuous tense.

    Returns:
        Confirmation message.
    """
    message = message.strip()

    if not message:
        return "No message provided."

    # Truncate very long messages to avoid spam
    MAX_LENGTH = 200
    if len(message) > MAX_LENGTH:
        message = message[: MAX_LENGTH - 3] + "..."

    # Check if callback is set
    callback = _progress_callback.get()
    if callback is None:
        return "Progress updates not available in this context."

    # Queue the message for sending by the progress update loop
    _queue_progress_message(message)

    return f"Progress update: {message}"


# ---------------------------------------------------------------------------
# Pending progress message queue
# ---------------------------------------------------------------------------
#
# The agent executes tools from a background thread.
# We queue progress messages and process them after the agent responds.
#
import threading

_pending_progress_lock = threading.Lock()
_pending_progress_by_key: dict[int, list[str]] = {}


def _pending_key_for_current_context() -> int | None:
    cb = _progress_callback.get()
    if cb is None:
        return None
    return id(cb)


def _get_pending_progress_ref() -> list[str]:
    """Get the per-message pending progress list, creating it if missing."""
    key = _pending_key_for_current_context()
    if key is None:
        return []

    with _pending_progress_lock:
        messages = _pending_progress_by_key.get(key)
        if messages is None:
            messages = []
            _pending_progress_by_key[key] = messages
        return messages


def _queue_progress_message(message: str) -> None:
    """Queue a progress message for later sending."""
    pending = _get_pending_progress_ref()
    if pending is not None:
        pending.append(message)


def get_pending_progress_messages() -> list[str]:
    """Get and clear the list of pending progress messages to send.

    Returns:
        List of progress message strings.
    """
    key = _pending_key_for_current_context()
    if key is None:
        return []

    with _pending_progress_lock:
        messages = list(_pending_progress_by_key.get(key, []))
        _pending_progress_by_key[key] = []
        return messages


def clear_pending_progress() -> None:
    """Clear pending progress messages for the current context."""
    key = _pending_key_for_current_context()
    if key is not None:
        with _pending_progress_lock:
            _pending_progress_by_key.pop(key, None)
