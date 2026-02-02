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


def _derive_skills_base_dir_from_template(template: str) -> str | None:
    """Derive the *base* skills directory from a template.

    Examples:
      - "/app/skills/{username}" -> "/app/skills"
      - "/app/skills" -> "/app/skills"
      - "./skills/{user_id}" -> "./skills"
    """

    raw = (template or "").strip()
    if not raw:
        return None

    # Keep placeholder normalization consistent with config.
    # (We duplicate the minimal behavior here so shell wrapper can work even
    # without a fully-constructed config object in context.)
    raw = raw.replace("[USERNAME]", "{username}").replace("[USER_ID]", "{user_id}")

    try:
        parts = list(Path(raw).parts)
    except Exception:
        return None

    # Find the first path segment containing a placeholder and truncate before it.
    for i, part in enumerate(parts):
        if "{username}" in part or "{user_id}" in part:
            if i <= 0:
                return None
            try:
                return str(Path(*parts[:i]))
            except Exception:
                return None

    # No placeholders -> treat as an already-base directory.
    return raw


def _ensure_mordecai_skills_base_dir_env() -> str | None:
    """Ensure MORDECAI_SKILLS_BASE_DIR is available for shell commands.

    Why this exists:
    - Some tool runners do not propagate the current process env to the
      subprocess environment (or sanitize it).
    - Some call sites may forget to call set_shell_env_context.
    - Skills often reference ${MORDECAI_SKILLS_BASE_DIR} in documented snippets.

    Returns the effective base dir if it could be determined.
    """

    existing = (os.environ.get("MORDECAI_SKILLS_BASE_DIR") or "").strip()
    if existing:
        return existing

    cfg = _config_var.get()
    uid = _current_user_id_var.get()

    # Best path: config + user id -> resolve per-user dir, then take its parent.
    if cfg is not None and uid:
        try:
            user_dir = resolve_user_skills_dir(cfg, str(uid), create=True)
            base_dir = str(user_dir.parent)
            if base_dir.strip():
                os.environ["MORDECAI_SKILLS_BASE_DIR"] = base_dir
                return base_dir
        except Exception:
            pass

    # Next best: config template (even without user id).
    if cfg is not None:
        try:
            raw_template = getattr(cfg, "user_skills_dir_template", None)
            derived = _derive_skills_base_dir_from_template(str(raw_template or ""))
            if derived:
                os.environ["MORDECAI_SKILLS_BASE_DIR"] = derived
                return derived
        except Exception:
            pass

    # Fallback: env template (common in container/docker setups).
    try:
        derived = _derive_skills_base_dir_from_template(
            os.environ.get("MORDECAI_SKILLS_BASE_DIR", "")
        )
        if derived:
            os.environ["MORDECAI_SKILLS_BASE_DIR"] = derived
            return derived
    except Exception:
        pass

    # Last resort: AGENT_SKILLS_BASE_DIR if present.
    try:
        raw = (os.environ.get("AGENT_SKILLS_BASE_DIR") or "").strip()
        if raw:
            os.environ["MORDECAI_SKILLS_BASE_DIR"] = raw
            return raw
    except Exception:
        pass

    return None


def _materialize_mordecai_skills_base_dir_in_command(command: str) -> str:
    """Inline ${MORDECAI_SKILLS_BASE_DIR} references in the command string.

    Some shell tool backends do not pass through env vars. When a command
    contains ${MORDECAI_SKILLS_BASE_DIR} (or $MORDECAI_SKILLS_BASE_DIR), inlining
    avoids reliance on the subprocess environment.
    """

    if not isinstance(command, str) or not command:
        return command

    if ("MORDECAI_SKILLS_BASE_DIR" not in command) and ("${" not in command):
        return command

    base_dir = _ensure_mordecai_skills_base_dir_env()
    if not base_dir:
        return command

    # Replace both ${VAR} and $VAR forms (conservative: only for this variable).
    cmd = command
    cmd = cmd.replace("${MORDECAI_SKILLS_BASE_DIR}", base_dir)
    cmd = re.sub(r"\$MORDECAI_SKILLS_BASE_DIR(?![A-Za-z0-9_])", base_dir, cmd)
    return cmd


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
            return 300
        return max(1, int(v))
    except Exception:
        return 300


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
    stream_output: bool = True,
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

    # IMPORTANT: trace context does not automatically propagate into new threads.
    # Capture it here so output streaming can log under the correct trace.
    trace_id = get_trace_id()
    from app.observability.trace_context import get_actor_id, set_trace

    actor_id = get_actor_id()

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_len = 0
    stderr_len = 0
    chunk_limit = 60_000  # keep only tail; consistent with _truncate

    def _append_tail(*, chunks: list[str], cur_len: int, text: str) -> int:
        if not text:
            return cur_len
        chunks.append(text)
        cur_len += len(text)
        # Drop from the front until we're within the limit.
        while chunks and cur_len > chunk_limit:
            removed = chunks.pop(0)
            cur_len -= len(removed)
        return cur_len

    def _stream_reader(*, which: str, stream) -> None:
        # Ensure logs correlate to the originating tool call.
        set_trace(trace_id=trace_id, actor_id=actor_id)
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break

                nonlocal stdout_len, stderr_len
                if which == "stdout":
                    stdout_len = _append_tail(chunks=stdout_chunks, cur_len=stdout_len, text=line)
                else:
                    stderr_len = _append_tail(chunks=stderr_chunks, cur_len=stderr_len, text=line)

                # Mark progress on actual output as well; helps stall detection.
                mark_progress("tool.shell.output")

                # Best-effort live logging. Keep it bounded and sanitized.
                if trace_id is not None:
                    trace_event(
                        "tool.shell.output",
                        stream=which,
                        text=line,
                        max_chars=400,
                    )
        except Exception:
            # Never let streaming break command execution.
            return

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
            bufsize=1,
            start_new_session=True,  # allows killing the whole process group
        )
        deadline = time.monotonic() + float(max(1, int(timeout_seconds)))
        hb = float(max(1, int(heartbeat_seconds)))

        t_out: threading.Thread | None = None
        t_err: threading.Thread | None = None
        if stream_output and proc.stdout is not None and proc.stderr is not None:
            t_out = threading.Thread(
                target=_stream_reader,
                kwargs={"which": "stdout", "stream": proc.stdout},
                name="tool-shell-stdout",
                daemon=True,
            )
            t_err = threading.Thread(
                target=_stream_reader,
                kwargs={"which": "stderr", "stream": proc.stderr},
                name="tool-shell-stderr",
                daemon=True,
            )
            t_out.start()
            t_err.start()

        last_hb = time.monotonic()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break

            rc = proc.poll()
            if rc is not None:
                returncode = int(rc)
                break

            # Still running.
            now = time.monotonic()
            if (now - last_hb) >= hb:
                mark_progress("tool.shell.heartbeat")
                last_hb = now

            time.sleep(min(0.2, remaining))

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
                proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass

            # Use a standard timeout exit code regardless of actual return.
            returncode = 124

        # Ensure reader threads drain remaining output.
        if stream_output:
            try:
                if t_out is not None:
                    t_out.join(timeout=1)
                if t_err is not None:
                    t_err.join(timeout=1)
            except Exception:
                pass

        # If streaming is disabled, fall back to a final communicate() to collect output.
        if not stream_output:
            try:
                out, err = proc.communicate(timeout=1)
                stdout = out or ""
                stderr = err or ""
            except Exception:
                pass

        # Combine streamed output buffers into strings.
        if stdout_chunks:
            stdout = "".join(stdout_chunks)
        if stderr_chunks:
            stderr = "".join(stderr_chunks)

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

    # Ensure MORDECAI_SKILLS_BASE_DIR is set for skills that reference it in
    # their documented shell snippets (notably himalaya).
    #
    # We do this here (not only in set_shell_env_context) because:
    # - some upstream tool runners may sanitize env between invocations
    # - hot-reload paths can reinitialize env state
    # - it must work even if the agent context setter failed silently
    try:
        _ensure_mordecai_skills_base_dir_env()
    except Exception:
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
        effective_command = _materialize_mordecai_skills_base_dir_in_command(effective_command)

        # Default to delegating to the upstream strands_tools shell implementation.
        # This preserves compatibility with skills and allows tests to monkeypatch
        # the base call.
        stream_output = bool(getattr(cfg, "shell_stream_output_enabled", False))

        # Streaming is only supported by the internal safe runner.
        # If streaming is enabled, force the safe runner even if shell_use_safe_runner=False.
        use_safe_runner = bool(getattr(cfg, "shell_use_safe_runner", False)) or stream_output

        # Start the heartbeat only while the underlying runner executes.
        hb_thread.start()

        if use_safe_runner:
            result = _safe_shell_run(
                command=effective_command,
                work_dir=work_dir,
                timeout_seconds=effective_timeout,
                heartbeat_seconds=heartbeat_s,
                stream_output=stream_output,
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
