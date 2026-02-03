"""file_write tool wrapper for compatibility across strands_tools versions.

Why this exists:
- Some strands_tools versions require a `tool` positional argument.
- Models sometimes call file_write immediately after reading skill docs.

We keep the public tool name as `file_write` so prompts/skills remain compatible.
"""

from __future__ import annotations

import time
from pathlib import Path
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


def _find_repo_root(*, start: Path) -> Path:
    """Best-effort repository root discovery (pyproject.toml heuristic)."""

    try:
        start = start.resolve()
        for p in [start, *start.parents]:
            if (p / "pyproject.toml").exists():
                return p
    except Exception:
        pass
    return Path.cwd()


def _scratchpad_root() -> Path:
    repo_root = _find_repo_root(start=Path(__file__))
    return (repo_root / "scratchpad").resolve()


def _ensure_scratchpad_path(path_raw: str) -> Path:
    root = _scratchpad_root()
    p = Path(str(path_raw)).expanduser()
    if not p.is_absolute():
        p = _find_repo_root(start=Path(__file__)) / p
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except Exception as e:
        raise ValueError(
            f"file_write is restricted to scratchpad/**. Refusing path: {resolved}"
        ) from e
    return resolved


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

    safe_path = str(_ensure_scratchpad_path(path))

    try:
        result = _call_base_file_write(path=safe_path, content=content, **kwargs)
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
