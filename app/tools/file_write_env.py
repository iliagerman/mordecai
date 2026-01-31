"""file_write tool wrapper for compatibility across strands_tools versions.

Why this exists:
- Some strands_tools versions require a `tool` positional argument.
- Models sometimes call file_write immediately after reading skill docs.

We keep the public tool name as `file_write` so prompts/skills remain compatible.
"""

from __future__ import annotations

import time
from typing import Any

from app.observability.trace_context import get_trace_id
from app.observability.trace_logging import trace_event

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


# Delegate to the official strands_tools file_write implementation.
# Depending on strands_tools version, it may be exported as:
# - a callable tool (`from strands_tools import file_write`)
# - a module containing a callable (`from strands_tools.file_write import file_write`)
try:  # pragma: no cover
    from strands_tools.file_write import file_write as _base_file_write  # type: ignore
except Exception:  # pragma: no cover
    from strands_tools import file_write as _base_file_write  # type: ignore


def _call_base_file_write(**kwargs: Any):
    def _call(fn):
        try:
            return fn(**kwargs)
        except TypeError as e:
            msg = str(e)
            if "required positional argument" not in msg or "tool" not in msg:
                raise

            tool_payload = {
                "toolUseId": "file_write_env",
                "name": "file_write",
                "input": {k: v for k, v in kwargs.items() if k != "tool"},
            }

            try:
                return fn(tool=tool_payload, **kwargs)
            except TypeError:
                return fn(tool_payload, **kwargs)

    if callable(_base_file_write):
        return _call(_base_file_write)
    inner = getattr(_base_file_write, "file_write", None)
    if callable(inner):
        return _call(inner)
    raise TypeError("strands_tools file_write implementation is not callable")


@tool(
    name="file_write",
    description=(
        "Write files to disk. This wrapper improves compatibility across strands_tools versions."
    ),
)
def file_write(
    path: str,
    content: str,
    **kwargs: Any,
):
    tool_t0 = time.perf_counter()
    if get_trace_id() is not None:
        trace_event(
            "tool.file_write.start",
            path=path,
            content_len=len(content) if isinstance(content, str) else None,
        )

    try:
        result = _call_base_file_write(path=path, content=content, **kwargs)
        if get_trace_id() is not None:
            trace_event(
                "tool.file_write.end",
                duration_ms=int((time.perf_counter() - tool_t0) * 1000),
            )
        return result
    except Exception as e:
        if get_trace_id() is not None:
            trace_event(
                "tool.file_write.error",
                duration_ms=int((time.perf_counter() - tool_t0) * 1000),
                error=str(e),
                error_type=type(e).__name__,
            )
        raise
