"""file_write tool wrapper for compatibility across strands_tools versions.

Why this exists:
- Some strands_tools versions require a `tool` positional argument.
- Models sometimes call file_write immediately after reading skill docs.

We keep the public tool name as `file_write` so prompts/skills remain compatible.
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


# Delegate to the official strands_tools file_write implementation.
# Depending on strands_tools version, it may be exported as:
# - a callable tool (`from strands_tools import file_write`)
# - a module containing a callable (`from strands_tools.file_write import file_write`)
try:  # pragma: no cover
    from strands_tools.file_write import file_write as _base_file_write  # type: ignore
except Exception:  # pragma: no cover
    from strands_tools import file_write as _base_file_write  # type: ignore


# Context variables for user-specific path access
_current_user_id_var: ContextVar[str | None] = ContextVar("file_write_user_id", default=None)
_config_var: ContextVar[Any] = ContextVar("file_write_config", default=None)


def set_file_write_context(*, user_id: str, config=None) -> None:
    """Set user context for file_write tool.

    Called by agent_service before creating the agent.
    """
    _current_user_id_var.set(user_id)
    _config_var.set(config)


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


def _allowed_roots() -> list[Path]:
    """Return list of allowed root paths for file operations."""
    roots = [_scratchpad_root()]

    # Add user's skill directory if context is available
    cfg = _config_var.get()
    uid = _current_user_id_var.get()
    if cfg is not None and uid is not None:
        try:
            user_skills_dir = resolve_user_skills_dir(cfg, uid, create=False)
            if user_skills_dir.exists():
                roots.append(user_skills_dir)
                # Also allow the skills base dir for shared skill access
                skills_base = Path(getattr(cfg, "skills_base_dir", "./skills")).expanduser()
                if not skills_base.is_absolute():
                    skills_base = _find_repo_root(start=Path(__file__)) / skills_base
                if skills_base.exists():
                    roots.append(skills_base.resolve())
        except Exception:
            pass

    return roots


def _ensure_allowed_path(path_raw: str) -> Path:
    """Ensure the path is within an allowed directory.

    Allowed directories:
    - scratchpad/**
    - User's skill directory (e.g., /app/skills/{username}/**)
    - Skills base directory (e.g., /app/skills/** for shared skills)
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
        f"file_write is restricted to: {', '.join(allowed_strs)}. Refusing path: {resolved}"
    )


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

    safe_path = str(_ensure_allowed_path(path))

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
