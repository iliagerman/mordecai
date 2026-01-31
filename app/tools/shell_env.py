"""Shell tool wrapper that hot-reloads skill env vars from secrets.yml.

Why this exists:
- Skill onboarding can persist env vars into secrets.yml.
- The agent must see updated env vars immediately for subsequent shell commands
  (no server/container restart).

We keep the public tool name as `shell` so skills continue to work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import refresh_runtime_env_from_secrets

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


def set_shell_env_context(*, user_id: str, secrets_path: str | Path) -> None:
    global _current_user_id, _secrets_path
    _current_user_id = user_id
    _secrets_path = Path(secrets_path)


@tool(
    name="shell",
    description=(
        "Run bash/shell commands. This wrapper hot-reloads skill env vars from secrets.yml "
        "so newly provided keys work immediately without restart."
    ),
)
def shell(**kwargs: Any):
    # kwargs are forwarded to strands_tools.shell. Typical args:
    # - command: str
    # - work_dir: str
    # - timeout_seconds: int
    try:
        refresh_runtime_env_from_secrets(
            secrets_path=_secrets_path,
            user_id=_current_user_id,
        )
    except Exception:
        # Never block shell execution if refresh fails.
        pass

    return _call_base_shell(**kwargs)
