"""Trace context (request/message correlation).

We intentionally keep this small and dependency-free so it can be used from:
- FastAPI middleware
- AgentService (per message)
- Tool wrappers (shell/file_read/etc)

The goal is to correlate logs across a single user message -> tool calls -> response.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar


_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_actor_id_var: ContextVar[str | None] = ContextVar("actor_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace(*, trace_id: str | None, actor_id: str | None = None) -> None:
    """Set trace context for the current execution context."""

    _trace_id_var.set(trace_id)
    if actor_id is not None:
        _actor_id_var.set(actor_id)


def get_trace_id() -> str | None:
    return _trace_id_var.get()


def get_actor_id() -> str | None:
    return _actor_id_var.get()
