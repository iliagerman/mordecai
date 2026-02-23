"""Tools for persisting skill-specific secrets/env vars.

Secrets are stored in the ``user_skill_secrets`` database table (per user).
The agent uses these tools during skill onboarding to store user-provided
values.  Secrets are never echoed back in responses.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from app.config import get_skill_env_vars, _is_exportable_env_key
from app.observability.trace_context import get_trace_id
from app.observability.trace_logging import trace_event

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


# ---------------------------------------------------------------------------
# Module-level context (set per-message by agent_creation.py)
# ---------------------------------------------------------------------------

_current_user_id: str | None = None
_config: Any = None
_skill_secret_dao: Any = None  # SkillSecretDAO instance


def set_skill_secrets_context(*, user_id: str, config: Any = None, dao: Any = None) -> None:
    """Set the per-request context for skill secrets tools.

    Called by ``agent_creation.py`` before each agent invocation.
    """
    global _current_user_id, _config, _skill_secret_dao
    _current_user_id = user_id
    _config = config
    _skill_secret_dao = dao


# ---------------------------------------------------------------------------
# Async bridge — tools run in threads, DAO methods are async
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from a sync tool running in a background thread.

    Uses the same nest_asyncio pattern as browser_tool.py.
    """
    import nest_asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            nest_asyncio.apply()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# In-memory cache updated alongside DB writes so shell() sees changes
# immediately without an async DB round-trip.
# ---------------------------------------------------------------------------

_cached_secrets: dict[str, Any] | None = None


def get_cached_skill_secrets() -> dict[str, Any]:
    """Return the in-memory cached secrets dict.

    Called by ``refresh_runtime_env_from_secrets`` in the shell tool hot-reload
    path.  Falls back to DB if cache is empty.
    """
    global _cached_secrets
    if _cached_secrets is not None:
        return _cached_secrets

    if _skill_secret_dao is not None and _current_user_id is not None:
        try:
            result = _run_async(_skill_secret_dao.get_secrets_data(_current_user_id))
            if isinstance(result, dict):
                _cached_secrets = result
                return _cached_secrets
        except Exception:
            pass

    return {}


def set_cached_skill_secrets(data: dict[str, Any]) -> None:
    """Replace the in-memory secrets cache (called at agent creation time)."""
    global _cached_secrets
    _cached_secrets = data


def _persist_and_cache(user_id: str, data: dict[str, Any]) -> None:
    """Write *data* to DB and update the in-memory cache."""
    global _cached_secrets
    _cached_secrets = data
    if _skill_secret_dao is not None:
        _run_async(_skill_secret_dao.upsert(user_id, data))

    # Hot-reload env vars into the running process.
    _hot_reload_env(user_id, data)


def _hot_reload_env(user_id: str, secrets_data: dict[str, Any]) -> None:
    """Inject uppercase env vars from *secrets_data* into ``os.environ``."""
    import os

    env_vars = get_skill_env_vars(
        secrets={"skills": secrets_data}, skill_name="", user_id=user_id,
    )
    for k, v in env_vars.items():
        if _is_exportable_env_key(k):
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(
    name="set_skill_env_vars",
    description=(
        "Persist env vars for a skill into the database. "
        "Provide values as env_json. Only UPPERCASE keys are exported as env vars."
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

    user_id = _current_user_id
    if user_id is None:
        return "User context not available."

    if _skill_secret_dao is None:
        return "Skill secrets not configured (missing DAO)."

    try:
        env = json.loads(env_json)
    except Exception:
        return 'env_json must be valid JSON (e.g., {"MY_KEY": "value"}).'

    if not isinstance(env, dict):
        return "env_json must decode to an object (a JSON dict)."

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

    # Load current secrets, update, persist.
    data = get_cached_skill_secrets()

    # Place under the skill_name group.
    block = data.setdefault(skill_name, {})
    if not isinstance(block, dict):
        block = {}
        data[skill_name] = block

    for k, v in env_vars.items():
        block[k] = v

    _persist_and_cache(user_id, data)

    keys = sorted(env_vars.keys())
    result = f"Saved {len(keys)} env var(s) for skill '{skill_name}'. Keys: {', '.join(keys)}"

    if get_trace_id() is not None:
        trace_event(
            "tool.set_skill_env_vars.end",
            duration_ms=int((time.perf_counter() - tool_t0) * 1000),
            skill_name=skill_name,
            keys=keys,
        )

    return result


@tool(
    name="set_skill_config",
    description=(
        "Persist structured config for a skill into the database. "
        "Provide values as config_json."
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

    user_id = _current_user_id
    if user_id is None:
        return "User context not available."

    if _skill_secret_dao is None:
        return "Skill secrets not configured (missing DAO)."

    try:
        cfg = json.loads(config_json)
    except Exception:
        return 'config_json must be valid JSON.'

    if not isinstance(cfg, dict):
        return "config_json must decode to an object (a JSON dict)."

    data = get_cached_skill_secrets()

    block = data.setdefault(skill_name, {})
    if not isinstance(block, dict):
        block = {}
        data[skill_name] = block

    for k, v in cfg.items():
        block[k] = v

    _persist_and_cache(user_id, data)

    keys = sorted([str(k) for k in cfg.keys() if str(k).strip()])
    result = (
        f"Saved config for skill '{skill_name}'. "
        f"Top-level keys: {', '.join(keys) if keys else '(none)'}"
    )

    if get_trace_id() is not None:
        trace_event(
            "tool.set_skill_config.end",
            duration_ms=int((time.perf_counter() - tool_t0) * 1000),
            skill_name=skill_name,
            keys=keys,
        )

    return result


@tool(
    name="unset_skill_config_keys",
    description=(
        "Remove one or more keys from a skill's config in the database. "
        'Provide keys_json as a JSON array of strings (e.g., ["OUTLOOK_EMAIL"]).'
    ),
)
def unset_skill_config_keys(
    skill_name: str,
    keys_json: str,
    apply_to: Literal["user", "global"] = "user",
) -> str:
    tool_t0 = time.perf_counter()
    if get_trace_id() is not None:
        trace_event(
            "tool.unset_skill_config_keys.start",
            skill_name=skill_name,
            apply_to=apply_to,
        )

    if not skill_name or not skill_name.strip():
        return "skill_name is required."

    user_id = _current_user_id
    if user_id is None:
        return "User context not available."

    if _skill_secret_dao is None:
        return "Skill secrets not configured (missing DAO)."

    try:
        raw = json.loads(keys_json)
    except Exception:
        return 'keys_json must be valid JSON (e.g., ["KEY1", "KEY2"]).'

    if not isinstance(raw, list):
        return "keys_json must decode to a JSON array."

    keys: list[str] = []
    for item in raw:
        k = str(item).strip()
        if k:
            keys.append(k)

    if not keys:
        return "No keys provided."

    data = get_cached_skill_secrets()
    block = data.get(skill_name)
    if not isinstance(block, dict):
        return f"No config found for skill '{skill_name}'."

    removed: list[str] = []
    for k in keys:
        if k in block:
            block.pop(k, None)
            removed.append(k)

    _persist_and_cache(user_id, data)

    if get_trace_id() is not None:
        trace_event(
            "tool.unset_skill_config_keys.end",
            duration_ms=int((time.perf_counter() - tool_t0) * 1000),
            skill_name=skill_name,
            removed=sorted(removed),
        )

    if not removed:
        return f"No keys removed for skill '{skill_name}' (none of the provided keys were set)."
    return f"Removed {len(removed)} key(s) for skill '{skill_name}': {', '.join(sorted(removed))}"
