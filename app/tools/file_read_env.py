"""file_read tool wrapper with a safe default `mode`.

Why this exists:
- `strands_tools.file_read` requires `mode`.
- Models sometimes omit it, causing a tool-schema error and potential retry loops.

We keep the public tool name as `file_read` so prompts/skills remain compatible.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from app.config import resolve_user_skills_dir
from app.observability.trace_context import get_trace_id
from app.observability.trace_logging import trace_event

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


# Delegate to the official strands_tools file_read implementation.
# Depending on strands_tools version, it may be exported as:
# - a callable tool (`from strands_tools import file_read`)
# - a module containing a callable (`from strands_tools.file_read import file_read`)
try:  # pragma: no cover
    from strands_tools.file_read import file_read as _base_file_read  # type: ignore
except Exception:  # pragma: no cover
    from strands_tools import file_read as _base_file_read  # type: ignore


# Context variables for user-specific path access
_current_user_id_var: ContextVar[str | None] = ContextVar("file_read_user_id", default=None)
_config_var: ContextVar[Any] = ContextVar("file_read_config", default=None)


def set_file_read_context(*, user_id: str, config=None) -> None:
    """Set user context for file_read tool.

    Called by agent_service before creating the agent.
    """
    _current_user_id_var.set(user_id)
    _config_var.set(config)


def _call_base_file_read(**kwargs: Any):
    def _call(fn):
        # Some strands_tools versions define: file_read(tool, path, mode, ...)
        # Others define: file_read(path, mode, ...)
        try:
            return fn(**kwargs)
        except TypeError as e:
            msg = str(e)
            if "required positional argument" not in msg or "tool" not in msg:
                raise

            tool_payload = {
                "toolUseId": "file_read_env",
                "name": "file_read",
                "input": {k: v for k, v in kwargs.items() if k != "tool"},
            }

            # Try keyword first, then positional.
            try:
                return fn(tool=tool_payload, **kwargs)
            except TypeError:
                return fn(tool_payload, **kwargs)

    if callable(_base_file_read):
        return _call(_base_file_read)
    inner = getattr(_base_file_read, "file_read", None)
    if callable(inner):
        return _call(inner)
    raise TypeError("strands_tools file_read implementation is not callable")


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


def _allowed_roots() -> list[Path]:
    """Return list of allowed root paths for file operations.

    Security: Only the user's own skill directory, shared skills directory,
    and workspace directory are allowed — never the entire skills_base_dir
    (which would expose other users' directories).
    The scratchpad lives inside the workspace so no separate root is needed.
    """
    roots: list[Path] = []

    cfg = _config_var.get()
    uid = _current_user_id_var.get()
    if cfg is not None and uid is not None:
        try:
            # User's own skill directory
            user_skills_dir = resolve_user_skills_dir(cfg, uid, create=False)
            if user_skills_dir.exists():
                roots.append(user_skills_dir)

            # Shared skills directory (read-only access for all users)
            shared_dir = Path(getattr(cfg, "shared_skills_dir", "./skills/shared")).expanduser()
            if not shared_dir.is_absolute():
                shared_dir = _find_repo_root(start=Path(__file__)) / shared_dir
            if shared_dir.resolve().exists():
                roots.append(shared_dir.resolve())

            # User's working directory
            work_base = Path(getattr(cfg, "working_folder_base_dir", "./workspace")).expanduser()
            if not work_base.is_absolute():
                work_base = _find_repo_root(start=Path(__file__)) / work_base
            user_work = (work_base / uid).resolve()
            if user_work.exists():
                roots.append(user_work)
        except Exception:
            pass

    return roots


def _ensure_allowed_path(path_raw: str) -> Path:
    """Ensure the path is within an allowed directory.

    Allowed directories:
    - User's own skill directory (e.g., /app/skills/{username}/**)
    - Shared skills directory (e.g., /app/skills/shared/**)
    - User's working directory (e.g., /app/workspace/{username}/**)
      (includes scratchpad at workspace/{username}/scratchpad/**)
    """
    p = Path(str(path_raw)).expanduser()
    if not p.is_absolute():
        p = _find_repo_root(start=Path(__file__)) / p
    resolved = p.resolve()

    roots = _allowed_roots()
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except Exception:
            continue

    allowed_strs = [str(r) for r in roots]
    raise ValueError(
        f"file_read is restricted to: {', '.join(allowed_strs)}. Refusing path: {resolved}"
    )


@tool(
    name="file_read",
    description=(
        "Read files from disk. Defaults mode='view' if omitted to prevent tool-schema errors."
    ),
)
def file_read(
    path: str,
    mode: str = "view",
    **kwargs: Any,
):
    """Read a file.

    Parameters intentionally accept **kwargs for compatibility across strands_tools versions.
    """

    tool_t0 = time.perf_counter()
    if get_trace_id() is not None:
        trace_event(
            "tool.file_read.start",
            path=path,
            mode=mode,
        )

    safe_path = str(_ensure_allowed_path(path))

    try:
        result = _call_base_file_read(path=safe_path, mode=mode, **kwargs)
        if get_trace_id() is not None:
            # Avoid emitting huge payloads; keep a preview and size.
            preview = None
            size = None
            if isinstance(result, str):
                preview = result
                size = len(result)
            elif isinstance(result, dict):
                # strands_tools often returns {"content": "..."}
                content = result.get("content")
                if isinstance(content, str):
                    preview = content
                    size = len(content)
            trace_event(
                "tool.file_read.end",
                duration_ms=int((time.perf_counter() - tool_t0) * 1000),
                content_preview=preview,
                content_len=size,
            )
        return result
    except Exception as e:
        if get_trace_id() is not None:
            trace_event(
                "tool.file_read.error",
                duration_ms=int((time.perf_counter() - tool_t0) * 1000),
                error=str(e),
                error_type=type(e).__name__,
            )
        raise
