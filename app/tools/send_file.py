"""Tool for sending files to the user via Telegram.

This tool allows the agent to send files (images, documents) back to
the user in the Telegram chat.

Concurrency note:
The agent can process multiple messages concurrently (across users and/or
prefetched messages). Tool state must therefore be *per message/task*.

We use :mod:`contextvars` so callbacks and pending files are isolated to the
current asyncio task (and propagate into the background thread used for agent
invocation via :func:`asyncio.to_thread`).
"""

import logging
from contextvars import ContextVar
from pathlib import Path
import threading
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TOOL_SPEC = {
    "name": "send_file",
    "description": (
        "Send a file to the user via Telegram. Use this after generating "
        "images or creating files that the user should receive. "
        "For images (.png, .jpg, .jpeg, .gif, .webp), they will be sent "
        "as photos with inline preview. Other files are sent as documents."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Full path to the file to send. Must be an existing file on the filesystem."
                    ),
                },
                "caption": {
                    "type": "string",
                    "description": ("Optional caption/message to include with the file"),
                },
            },
            "required": ["file_path"],
        }
    },
}

# Image extensions that should be sent as photos
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Per-task references - set by message processor before agent runs
_send_file_callback: ContextVar[Callable[[str, str | None], Awaitable[bool]] | None] = ContextVar(
    "send_file_callback", default=None
)
_send_photo_callback: ContextVar[Callable[[str, str | None], Awaitable[bool]] | None] = ContextVar(
    "send_photo_callback", default=None
)


# ---------------------------------------------------------------------------
# Pending file queue
# ---------------------------------------------------------------------------
#
# The agent executes tools from a background thread.
#
# We previously attempted to share a mutable list via ContextVar between the
# parent asyncio task and the background thread. In practice, depending on the
# runtime and the agent/tool execution strategy, ContextVar values can fail to
# share exactly as expected.
#
# We *do* reliably see the send callbacks propagate into the tool execution
# context. Therefore we key the pending queue off the identity of the callback
# function object. Each message gets fresh callback callables (defined in
# app/sqs/message_processor.py), so this key is effectively per-message.
_pending_files_lock = threading.Lock()
_pending_files_by_key: dict[int, list[dict[str, object]]] = {}


def _pending_key_for_current_context() -> int | None:
    cb = _send_file_callback.get()
    if cb is None:
        return None
    return id(cb)


def _get_pending_files_ref() -> list[dict[str, object]]:
    """Get the per-message pending file list, creating it if missing."""
    key = _pending_key_for_current_context()
    if key is None:
        # No callbacks means we're not in a sending-capable context.
        return []

    with _pending_files_lock:
        files = _pending_files_by_key.get(key)
        if files is None:
            files = []
            _pending_files_by_key[key] = files
        return files


def set_send_callbacks(
    send_file: Callable[[str, str | None], Awaitable[bool]],
    send_photo: Callable[[str, str | None], Awaitable[bool]],
) -> None:
    """Set the callbacks for sending files.

    Called by message processor before creating the agent.

    Args:
        send_file: Async callback to send a document.
        send_photo: Async callback to send a photo.
    """
    _send_file_callback.set(send_file)
    _send_photo_callback.set(send_photo)

    # Ensure pending queue exists and is cleared for this callback key.
    key = _pending_key_for_current_context()
    if key is None:
        return
    with _pending_files_lock:
        existing = _pending_files_by_key.get(key)
        if existing is None:
            _pending_files_by_key[key] = []
        else:
            existing.clear()


def clear_send_callbacks() -> None:
    """Clear the send callbacks after processing."""
    key = _pending_key_for_current_context()
    if key is not None:
        with _pending_files_lock:
            _pending_files_by_key.pop(key, None)
    _send_file_callback.set(None)
    _send_photo_callback.set(None)


def send_file(tool: dict, **kwargs: Any) -> dict:
    """Send a file to the user via Telegram.

    Args:
        tool: Tool invocation data with toolUseId and input.
        **kwargs: Additional context.

    Returns:
        Tool result with success/error status.
    """
    tool_use_id = tool["toolUseId"]
    tool_input = tool.get("input", {})
    file_path = tool_input.get("file_path", "").strip()
    caption = tool_input.get("caption", "").strip() or None

    if not file_path:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "No file path provided."}],
        }

    path = Path(file_path)
    if not path.exists():
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": f"File not found: {file_path}"}],
        }

    if not path.is_file():
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": f"Path is not a file: {file_path}"}],
        }

    # Check if callbacks are set
    if _send_file_callback.get() is None or _send_photo_callback.get() is None:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "File sending not available in this context."}],
        }

    # Determine if this is an image
    is_image = path.suffix.lower() in IMAGE_EXTENSIONS

    # Queue the file send (will be executed after agent response)
    pending = _get_pending_files_ref()
    if pending is None:
        # Defensive (should not happen), but avoid claiming success.
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "File sending not available in this context."}],
        }

    pending.append(
        {
            "path": str(path),
            "caption": caption,
            "is_image": is_image,
        }
    )

    file_type = "image" if is_image else "file"
    return {
        "toolUseId": tool_use_id,
        "status": "success",
        "content": [{"text": f"Queued {file_type} for sending: {path.name}"}],
    }


def get_pending_files() -> list[dict]:
    """Get and clear the list of pending files to send.

    Returns:
        List of file dicts with path, caption, is_image.
    """
    key = _pending_key_for_current_context()
    if key is None:
        return []

    with _pending_files_lock:
        files = list(_pending_files_by_key.get(key, []))
        _pending_files_by_key[key] = []
        return files
