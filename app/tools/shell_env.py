"""Shell tool wrapper that hot-reloads skill env vars from secrets.yml.

Why this exists:
- Skill onboarding can persist env vars into secrets.yml.
- The agent must see updated env vars immediately for subsequent shell commands
  (no server/container restart).

We keep the public tool name as `shell` so skills continue to work.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from app.config import refresh_runtime_env_from_secrets, resolve_user_skills_dir
from app.observability.health_state import mark_progress
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


_current_user_id_var: ContextVar[str | None] = ContextVar("shell_env_current_user_id", default=None)
_secrets_path_var: ContextVar[Path] = ContextVar(
    "shell_env_secrets_path", default=Path("secrets.yml")
)
_config_var: ContextVar[object | None] = ContextVar("shell_env_config", default=None)


def _stdin_is_tty() -> bool:
    """Best-effort check for an interactive TTY.

    The upstream `strands_tools.shell` supports an interactive PTY mode that can
    misbehave (or block indefinitely) when stdin is not a real TTY.

    In the agent runtime, tool invocations are typically headless; forcing
    non-interactive mode avoids hangs from stdin/select/waitpid edge cases.
    """

    try:
        return bool(getattr(sys.stdin, "isatty", lambda: False)())
    except Exception:
        return False


def set_shell_env_context(*, user_id: str, secrets_path: str | Path, config=None) -> None:
    # ContextVars make this safe under concurrent async tasks and also propagate
    # into asyncio.to_thread() calls.
    _current_user_id_var.set(user_id)
    _secrets_path_var.set(Path(secrets_path))
    _config_var.set(config)

    # Provide a portable skills base dir for SKILL.md examples.
    #
    # Many skills historically hard-coded `/app/skills/...` (container path).
    # In dev, the skills base dir differs, so examples should reference:
    #   ${MORDECAI_SKILLS_BASE_DIR}/<USERNAME>/...
    #
    # We compute this from config in a way that respects user_skills_dir_template.
    try:
        if config is not None:
            user_dir = resolve_user_skills_dir(config, str(user_id), create=True)
            base_dir = user_dir.parent
            os.environ["MORDECAI_SKILLS_BASE_DIR"] = str(base_dir)
    except Exception:
        # Never fail agent startup due to env convenience vars.
        pass


def _default_shell_timeout_seconds() -> int:
    cfg = _config_var.get()
    try:
        v = getattr(cfg, "shell_default_timeout_seconds", None)
        if v is None:
            return 180
        return max(1, int(v))
    except Exception:
        return 180


def _choose_shell_executable() -> str | None:
    # Prefer bash for consistent behavior with skills that use bash-isms.
    for cand in ("/bin/bash", "/usr/bin/bash"):
        try:
            if Path(cand).exists():
                return cand
        except Exception:
            continue
    return None


def _truncate(s: str | None, limit: int = 60_000) -> str | None:
    if not isinstance(s, str):
        return None
    if len(s) <= limit:
        return s
    # Keep the tail since it often contains the error.
    return s[-limit:]


def _maybe_prefix_himalaya_config(command: str) -> str:
    """Normalize common Himalaya command footguns.

    Historical note: this function used to prefix `export HIMALAYA_CONFIG=...`.
    Prefixing is now documented in the himalaya skill's SKILL.md, so we do not
    synthesize exports here.

    However, models sometimes emit shell strings with backslash-escaped quotes
    (e.g. `export HIMALAYA_CONFIG=\"/app/skills/u/himalaya.toml\"`). In bash,
    that sets the env var to a value that *includes* quote characters, which
    causes Himalaya to report the config file as missing and then prompt.

    We defensively normalize this mistake for commands that appear to invoke
    `himalaya`.
    """

    if not isinstance(command, str) or not command:
        return command

    cmd = command
    # Only touch commands that likely invoke himalaya.
    if not (cmd.startswith("himalaya ") or " himalaya " in f" {cmd} "):
        return cmd

    # If the model used JSON-style escaping, those backslashes can leak into the
    # actual shell command string.
    if '\\"' in cmd:
        cmd = cmd.replace('\\"', '"')

    # Also fix the common pattern where HIMALAYA_CONFIG export is escaped.
    # Example input:
    #   export HIMALAYA_CONFIG=\"/app/skills/u/himalaya.toml\" && himalaya ...
    # Desired:
    #   export HIMALAYA_CONFIG="/app/skills/u/himalaya.toml" && himalaya ...
    cmd = re.sub(
        r"\bexport\s+HIMALAYA_CONFIG=\\\"([^\"]+)\\\"",
        r"export HIMALAYA_CONFIG=\"\1\"",
        cmd,
    )

    return cmd


def _safe_shell_run(
    *,
    command: str,
    work_dir: str | None,
    timeout_seconds: int,
    heartbeat_seconds: int = 15,
) -> dict[str, Any]:
    """Run a shell command safely with a hard timeout.

    Why not delegate to strands_tools.shell?
    - Some upstream versions can hang in PTY/interactive mode.
    - We need a kill-on-timeout behavior to keep the service responsive.

    Output shape is intentionally compatible with strands_tools' common dict form.
    """

    cwd = work_dir or None
    shell_exe = _choose_shell_executable()

    # Make tools non-interactive by default.
    env = os.environ.copy()
    env.setdefault("BYPASS_TOOL_CONSENT", "true")
    env.setdefault("STRANDS_NON_INTERACTIVE", "true")

    t0 = time.perf_counter()
    timed_out = False
    stdout = ""
    stderr = ""
    returncode: int | None = None

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            executable=shell_exe,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # allows killing the whole process group
        )
        deadline = time.monotonic() + float(max(1, int(timeout_seconds)))
        hb = float(max(1, int(heartbeat_seconds)))

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break

            step = min(hb, remaining)
            try:
                out, err = proc.communicate(timeout=step)
                stdout = out or ""
                stderr = err or ""
                returncode = int(proc.returncode or 0)
                break
            except subprocess.TimeoutExpired:
                # Still running. Emit a heartbeat so stall detection doesn't fire.
                mark_progress("tool.shell.heartbeat")
                continue

        if timed_out:
            # Kill the full process group to avoid orphaned children.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass

            # Give it a brief grace period, then SIGKILL.
            try:
                out, err = proc.communicate(timeout=2)
                stdout = out or ""
                stderr = err or ""
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    out, err = proc.communicate(timeout=2)
                    stdout = out or ""
                    stderr = err or ""
                except Exception:
                    pass

            returncode = 124  # common timeout exit code

    finally:
        # Best-effort: ensure returncode is populated.
        if proc is not None and returncode is None:
            try:
                returncode = int(proc.poll() or 0)
            except Exception:
                returncode = 0

    dt_ms = int((time.perf_counter() - t0) * 1000)
    stdout_t = _truncate(stdout)
    stderr_t = _truncate(stderr)

    if timed_out:
        msg = (
            f"Command timed out after {timeout_seconds}s. "
            "If this is expected, pass a larger timeout_seconds from the skill."
        )
        content_text = (stderr_t or "") + ("\n" if stderr_t else "") + msg
        return {
            "status": "error",
            "returncode": returncode,
            "stdout": stdout_t,
            "stderr": content_text,
            "timed_out": True,
            "duration_ms": dt_ms,
            "content": [{"text": content_text}],
        }

    # Success or normal non-zero exit.
    status = "success" if returncode == 0 else "error"
    combined = (stdout_t or "") + ("\n" if stdout_t and stderr_t else "") + (stderr_t or "")
    if not combined:
        combined = "(no output)"
    return {
        "status": status,
        "returncode": returncode,
        "stdout": stdout_t,
        "stderr": stderr_t,
        "timed_out": False,
        "duration_ms": dt_ms,
        "content": [{"text": combined}],
    }


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

    # Normalize timeout argument naming across strands_tools versions.
    # The upstream tool uses `timeout`, while some callers/models use `timeout_seconds`.
    # Priority:
    #   explicit timeout_seconds param > kwargs.timeout
    effective_timeout: int | None = None
    if timeout_seconds is not None:
        effective_timeout = int(timeout_seconds)
    else:
        raw_timeout = kwargs.get("timeout")
        if raw_timeout is not None:
            try:
                effective_timeout = int(raw_timeout)
            except Exception:
                effective_timeout = None

    # Force non-interactive mode when stdin is not a TTY.
    # This prevents the upstream tool's interactive PTY implementation from
    # attempting to read from stdin in headless environments (where it may
    # raise or block in a way that bypasses the timeout).
    effective_non_interactive = bool(non_interactive) or (not _stdin_is_tty())
    tool_t0 = time.perf_counter()

    # Heartbeat so stall detection can restart us if needed.
    mark_progress("tool.shell.start")

    if get_trace_id() is not None:
        trace_event(
            "tool.shell.start",
            command=command,
            work_dir=work_dir,
            timeout_seconds=timeout_seconds,
            effective_timeout=effective_timeout,
            ignore_errors=ignore_errors,
            parallel=parallel,
            non_interactive=non_interactive,
            effective_non_interactive=effective_non_interactive,
        )

    try:
        refresh_runtime_env_from_secrets(
            secrets_path=_secrets_path_var.get(),
            user_id=_current_user_id_var.get(),
            config=_config_var.get(),
        )
    except Exception:
        # Never block shell execution if refresh fails.
        pass

    # Guardrail: certain CLIs (notably himalaya over IMAP/SMTP) can block for a long time
    # on network/auth issues. If a skill forgets to pass a timeout, apply a conservative
    # default so the agent doesn't hang indefinitely.
    cmd = (command or "").strip()
    if effective_timeout is None:
        # Detect common patterns like:
        #   himalaya ...
        #   export HIMALAYA_CONFIG=... && himalaya ...
        if cmd.startswith("himalaya ") or " himalaya " in f" {cmd} ":
            effective_timeout = 45
        else:
            # Global default: prevent *any* command from hanging indefinitely.
            effective_timeout = _default_shell_timeout_seconds()

    # Safety: if the model tries to pass a crazy timeout, clamp it.
    cfg = _config_var.get()
    try:
        max_timeout = int(getattr(cfg, "shell_max_timeout_seconds", 3600))
    except Exception:
        max_timeout = 3600

    max_timeout = max(1, max_timeout)
    try:
        effective_timeout = max(1, min(int(effective_timeout), max_timeout))
    except Exception:
        effective_timeout = _default_shell_timeout_seconds()

    # Emit periodic progress heartbeats while the underlying runner executes.
    # This avoids long-running commands being flagged as "stalled".
    try:
        heartbeat_s = int(getattr(cfg, "shell_progress_heartbeat_seconds", 15))
    except Exception:
        heartbeat_s = 15
    heartbeat_s = max(1, heartbeat_s)

    heartbeat_stop = threading.Event()

    def _heartbeat_loop() -> None:
        while not heartbeat_stop.wait(timeout=float(heartbeat_s)):
            mark_progress("tool.shell.heartbeat")

    forwarded: dict[str, Any] = {
        "command": command,
        "ignore_errors": ignore_errors,
        "parallel": parallel,
        "non_interactive": effective_non_interactive,
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

    hb_thread = threading.Thread(target=_heartbeat_loop, name="tool-shell-heartbeat", daemon=True)

    try:
        effective_command = _maybe_prefix_himalaya_config(command)

        # Default to delegating to the upstream strands_tools shell implementation.
        # This preserves compatibility with skills and allows tests to monkeypatch
        # the base call.
        use_safe_runner = bool(getattr(cfg, "shell_use_safe_runner", False))

        # Start the heartbeat only while the underlying runner executes.
        hb_thread.start()

        if use_safe_runner:
            result = _safe_shell_run(
                command=effective_command,
                work_dir=work_dir,
                timeout_seconds=effective_timeout,
                heartbeat_seconds=heartbeat_s,
            )
        else:
            forwarded["command"] = effective_command
            result = _call_base_shell(**forwarded)

        # Heartbeat again after completion.
        mark_progress("tool.shell.end")
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
    finally:
        heartbeat_stop.set()
