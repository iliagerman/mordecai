"""Shell tool wrapper that hot-reloads skill env vars from secrets.yml.

Why this exists:
- Skill onboarding can persist env vars into secrets.yml.
- The agent must see updated env vars immediately for subsequent shell commands
  (no server/container restart).

We keep the public tool name as `shell` so skills continue to work.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.config import refresh_runtime_env_from_secrets
from app.observability.trace_context import get_trace_id
from app.observability.trace_logging import trace_event

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


# Delegate to the official strands_tools shell implementation.
# Depending on strands_tools version, shell may be exported as:
# - a callable tool (`from strands_tools import shell`)
# - a module containing a callable (`from strands_tools.shell import shell`)
try:  # pragma: no cover
    from strands_tools.shell import shell as _base_shell  # type: ignore
except Exception:  # pragma: no cover
    from strands_tools import shell as _base_shell  # type: ignore


def _call_base_shell(**kwargs: Any):
    if callable(_base_shell):
        return _base_shell(**kwargs)
    inner = getattr(_base_shell, "shell", None)
    if callable(inner):
        return inner(**kwargs)
    raise TypeError("strands_tools shell implementation is not callable")


_current_user_id: str | None = None
_secrets_path: Path = Path("secrets.yml")
_config = None


def set_shell_env_context(*, user_id: str, secrets_path: str | Path, config=None) -> None:
    global _current_user_id, _secrets_path, _config
    _current_user_id = user_id
    _secrets_path = Path(secrets_path)
    _config = config


@tool(
    name="shell",
    description=(
        "Run bash/shell commands. This wrapper hot-reloads skill env vars from secrets.yml "
        "so newly provided keys work immediately without restart."
    ),
)
def shell(
    command: str,
    work_dir: str | None = None,
    timeout_seconds: int | None = None,
    ignore_errors: bool = False,
    parallel: bool = False,
    non_interactive: bool = False,
    **kwargs: Any,
):
    """Run a shell command with best-effort env refresh.

    We keep a mostly stable surface area for skills:
    - required: command
    - common: work_dir
    - optional: timeout_seconds
    - pass-through: **kwargs for compatibility with different strands_tools versions
    """

    # kwargs are forwarded to strands_tools.shell. Typical args:
    # - command: str
    # - work_dir: str
    # - timeout: int

    # Guardrail: certain CLIs (notably himalaya over IMAP/SMTP) can block for a long time
    # on network/auth issues. If a skill forgets to pass a timeout, apply a conservative
    # default so the agent doesn't hang indefinitely.
    # Normalize timeout argument naming across strands_tools versions.
    # The upstream tool uses `timeout`, while some callers/models use `timeout_seconds`.
    # Priority:
    #   explicit timeout_seconds param > kwargs.timeout
    effective_timeout: int | None = None
    if timeout_seconds is not None:
        effective_timeout = int(timeout_seconds)
    elif kwargs.get("timeout") is not None:
        try:
            effective_timeout = int(kwargs.get("timeout"))
        except Exception:
            effective_timeout = None

    # Guardrail: certain CLIs (notably himalaya over IMAP/SMTP) can block for a long time
    # on network/auth issues. If a skill forgets to pass a timeout, apply a conservative
    # default so the agent doesn't hang indefinitely.
    cmd = (command or "").strip()
    if effective_timeout is None:
        # Detect common patterns like:
        #   himalaya ...
        #   HIMALAYA_CONFIG=... himalaya ...
        if cmd.startswith("himalaya ") or " himalaya " in f" {cmd} ":
            effective_timeout = 45
    tool_t0 = time.perf_counter()

    if get_trace_id() is not None:
        trace_event(
            "tool.shell.start",
            command=command,
            work_dir=work_dir,
            timeout_seconds=timeout_seconds,
            ignore_errors=ignore_errors,
            parallel=parallel,
            non_interactive=non_interactive,
        )

    try:
        refresh_runtime_env_from_secrets(
            secrets_path=_secrets_path,
            user_id=_current_user_id,
            config=_config,
        )
    except Exception:
        # Never block shell execution if refresh fails.
        pass

    forwarded: dict[str, Any] = {
        "command": command,
        "ignore_errors": ignore_errors,
        "parallel": parallel,
        "non_interactive": non_interactive,
        **kwargs,
    }

    # Some models try to pass a literal `kwargs` field. Never forward it.
    forwarded.pop("kwargs", None)
    # Also normalize/remove other timeout spellings that could be injected.
    forwarded.pop("timeout_seconds", None)
    forwarded.pop("timeoutSeconds", None)
    forwarded.pop("timeout", None)
    if work_dir is not None:
        forwarded["work_dir"] = work_dir
    if effective_timeout is not None:
        forwarded["timeout"] = effective_timeout

    try:
        result = _call_base_shell(**forwarded)
        if get_trace_id() is not None:
            # Try to normalize common strands_tools result shapes.
            exit_code = None
            stdout = None
            stderr = None
            if isinstance(result, dict):
                exit_code = result.get("exit_code") or result.get("returncode")
                stdout = result.get("stdout")
                stderr = result.get("stderr")

            trace_event(
                "tool.shell.end",
                duration_ms=int((time.perf_counter() - tool_t0) * 1000),
                exit_code=exit_code,
                stdout_preview=stdout,
                stderr_preview=stderr,
                stdout_len=len(stdout) if isinstance(stdout, str) else None,
                stderr_len=len(stderr) if isinstance(stderr, str) else None,
            )
        return result
    except Exception as e:
        if get_trace_id() is not None:
            trace_event(
                "tool.shell.error",
                duration_ms=int((time.perf_counter() - tool_t0) * 1000),
                error=str(e),
                error_type=type(e).__name__,
            )
        raise
