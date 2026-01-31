"""Tools for persisting skill-specific secrets/env vars.

These tools are used by the agent during skill onboarding to store user-provided
values into secrets.yml under the `skills:` section.

Secrets are never echoed back in responses.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from app.config import (
    refresh_runtime_env_from_secrets,
    resolve_user_skills_secrets_path,
    upsert_skill_config,
    upsert_skill_env_vars,
)
from app.observability.trace_context import get_trace_id
from app.observability.trace_logging import trace_event

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


_current_user_id: str | None = None
_secrets_path: Path = Path("secrets.yml")
_config = None


def set_skill_secrets_context(*, user_id: str, secrets_path: str | Path, config=None) -> None:
    global _current_user_id, _secrets_path, _config
    _current_user_id = user_id
    _secrets_path = Path(secrets_path)
    _config = config


@tool(
    name="set_skill_env_vars",
    description=(
        "Persist env vars for a skill into secrets.yml under skills.<skill>.env (global) "
        "or skills.<skill>.users.<user_id>.env (per-user). Provide values as env_json."
    ),
)
def set_skill_env_vars(
    skill_name: str,
    env_json: str,
    apply_to: Literal["user", "global"] = "user",
) -> str:
    tool_t0 = time.perf_counter()
    if get_trace_id() is not None:
        trace_event(
            "tool.set_skill_env_vars.start",
            skill_name=skill_name,
            apply_to=apply_to,
        )

    if not skill_name or not skill_name.strip():
        return "skill_name is required."

    if apply_to == "user" and _current_user_id is None:
        return "User context not available."

    try:
        env = json.loads(env_json)
    except Exception:
        return 'env_json must be valid JSON (e.g., {"MY_KEY": "value"}).'

    if not isinstance(env, dict):
        return "env_json must decode to an object (a JSON dict)."

    # Normalize to string->string and ignore nulls.
    env_vars: dict[str, str] = {}
    for k, v in env.items():
        if v is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        env_vars[key] = str(v)

    if not env_vars:
        return "No env vars provided."

    user_id = None if apply_to == "global" else _current_user_id

    # Per-user values live in skills/<user>/skills_secrets.yml.
    target_path = _secrets_path
    if user_id is not None:
        if _config is None:
            return "Skill secrets context misconfigured (missing config)."
        target_path = resolve_user_skills_secrets_path(_config, user_id)

    upsert_skill_env_vars(
        secrets_path=target_path,
        skill_name=skill_name,
        env_vars=env_vars,
        user_id=None,
    )

    # Hot-reload into the running process so subsequent shell/subprocess calls see it.
    refresh_runtime_env_from_secrets(
        secrets_path=_secrets_path,
        user_id=user_id,
        skill_names=[skill_name],
        config=_config,
    )

    # Do not echo values.
    keys = sorted(env_vars.keys())
    scope = "global" if user_id is None else f"user:{user_id}"
    result = (
        f"Saved {len(keys)} env var(s) for skill '{skill_name}' ({scope}). Keys: {', '.join(keys)}"
    )

    if get_trace_id() is not None:
        trace_event(
            "tool.set_skill_env_vars.end",
            duration_ms=int((time.perf_counter() - tool_t0) * 1000),
            skill_name=skill_name,
            apply_to=apply_to,
            keys=keys,
            scope=scope,
        )

    return result


@tool(
    name="set_skill_config",
    description=(
        "Persist structured config for a skill into secrets.yml under skills.<skill> (global) "
        "or skills.<skill>.users.<user_id> (per-user). Provide values as config_json. "
        "If config_json includes a 'path' key, Mordecai will best-effort materialize the config file "
        "at that path (e.g., TOML)."
    ),
)
def set_skill_config(
    skill_name: str,
    config_json: str,
    apply_to: Literal["user", "global"] = "user",
) -> str:
    tool_t0 = time.perf_counter()
    if get_trace_id() is not None:
        trace_event(
            "tool.set_skill_config.start",
            skill_name=skill_name,
            apply_to=apply_to,
        )

    if not skill_name or not skill_name.strip():
        return "skill_name is required."

    if apply_to == "user" and _current_user_id is None:
        return "User context not available."

    try:
        cfg = json.loads(config_json)
    except Exception:
        return 'config_json must be valid JSON (e.g., {"path": "/tmp/config.toml", "email": "u@example.com"}).'

    if not isinstance(cfg, dict):
        return "config_json must decode to an object (a JSON dict)."

    user_id = None if apply_to == "global" else _current_user_id

    target_path = _secrets_path
    if user_id is not None:
        if _config is None:
            return "Skill secrets context misconfigured (missing config)."
        target_path = resolve_user_skills_secrets_path(_config, user_id)

    upsert_skill_config(
        secrets_path=target_path,
        skill_name=skill_name,
        config_data=cfg,
        user_id=None,
    )

    # Best-effort: materialize config files and refresh env.
    refresh_runtime_env_from_secrets(
        secrets_path=_secrets_path,
        user_id=user_id,
        skill_names=[skill_name],
        config=_config,
    )

    # Do not echo values.
    keys = sorted([str(k) for k in cfg.keys() if str(k).strip()])
    scope = "global" if user_id is None else f"user:{user_id}"
    result = (
        f"Saved config for skill '{skill_name}' ({scope}). "
        f"Top-level keys: {', '.join(keys) if keys else '(none)'}"
    )

    if get_trace_id() is not None:
        trace_event(
            "tool.set_skill_config.end",
            duration_ms=int((time.perf_counter() - tool_t0) * 1000),
            skill_name=skill_name,
            apply_to=apply_to,
            keys=keys,
            scope=scope,
        )

    return result
