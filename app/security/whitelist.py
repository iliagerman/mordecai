"""User whitelist enforcement utilities.

This module centralizes whitelist checks for both HTTP handlers (FastAPI)
and Telegram bot handlers.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException, status

CONTACT_EMAIL = "iliag@sela.co.il"
DEFAULT_FORBIDDEN_DETAIL = f"contact {CONTACT_EMAIL}"


_LIVE_ALLOWED_USERS_LOCK = threading.Lock()
# Cache key: resolved secrets path (string)
# Cache value: (mtime_ns, size, allowed_users_list)
_LIVE_ALLOWED_USERS_CACHE: dict[str, tuple[int, int, list[str]]] = {}


def _coerce_allowed_users(value: Any) -> list[str]:
    """Coerce YAML value into a clean list[str].

    Accepts:
    - list[str]
    - single string (treated as one user)
    - anything else => []
    """
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append(s)
        return out
    return []


def get_allowed_users_from_secrets(secrets_path: str | Path) -> list[str]:
    """Load allowed_users directly from secrets.yml.

    This is intentionally *not* wired through AgentConfig so that updates to
    secrets.yml are reflected immediately in authz checks without a restart.

    Caching: reuses the parsed list until the file's (mtime_ns, size) changes.
    """
    path = Path(secrets_path)
    try:
        resolved_key = str(path.resolve())
    except Exception:
        resolved_key = str(path)

    try:
        st = path.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        size = int(st.st_size)
    except FileNotFoundError:
        # Missing secrets.yml => behave like "no whitelist configured".
        with _LIVE_ALLOWED_USERS_LOCK:
            _LIVE_ALLOWED_USERS_CACHE[resolved_key] = (0, 0, [])
        return []
    except Exception:
        return []

    with _LIVE_ALLOWED_USERS_LOCK:
        cached = _LIVE_ALLOWED_USERS_CACHE.get(resolved_key)
        if cached is not None and cached[0] == mtime_ns and cached[1] == size:
            return list(cached[2])

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            allowed_users = []
        else:
            allowed_users = _coerce_allowed_users(raw.get("allowed_users"))
    except Exception:
        # If the file is mid-write or malformed, fail open (dev-friendly).
        allowed_users = []

    with _LIVE_ALLOWED_USERS_LOCK:
        _LIVE_ALLOWED_USERS_CACHE[resolved_key] = (mtime_ns, size, list(allowed_users))

    return allowed_users


class LiveAllowedUsers:
    """Iterable allowed_users provider that reloads secrets.yml on demand."""

    def __init__(self, secrets_path: str | Path) -> None:
        self.secrets_path = Path(secrets_path)

    def get(self) -> list[str]:
        return get_allowed_users_from_secrets(self.secrets_path)

    def __iter__(self):
        return iter(self.get())


def live_allowed_users(secrets_path: str | Path) -> LiveAllowedUsers:
    """Factory for a live-reloading allowed_users iterable."""
    return LiveAllowedUsers(secrets_path)


def _normalize_user_id(user_id: str) -> str:
    # Accept common formats:
    # - Telegram usernames may be provided as "@name" or "name".
    # - Numeric IDs should pass through untouched (aside from strip/lower).
    normalized = (user_id or "").strip().lower()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    return normalized


def normalize_allowed_users(allowed_users: Iterable[str] | None) -> set[str]:
    if allowed_users is None:
        return set()

    # Convert to list() so providers (e.g., LiveAllowedUsers) are evaluated now.
    items = list(allowed_users)
    if not items:
        return set()

    return {_normalize_user_id(u) for u in items if _normalize_user_id(u)}


def is_whitelisted(user_id: str, allowed_users: Iterable[str] | None) -> bool:
    allowed = normalize_allowed_users(allowed_users)
    if not allowed:
        # Empty whitelist => allow all (safe default for dev/tests)
        return True
    return _normalize_user_id(user_id) in allowed


def enforce_whitelist_or_403(
    user_id: str,
    allowed_users: Iterable[str] | None,
    *,
    detail: str = DEFAULT_FORBIDDEN_DETAIL,
    request: Any | None = None,
) -> None:
    if is_whitelisted(user_id, allowed_users):
        return

    # Record which condition failed so 403 logs can show *why* we rejected.
    try:
        allowed = normalize_allowed_users(allowed_users)
        if request is not None:
            request.state.authz_failure = {
                "code": "WHITELIST_DENY",
                "conditions": [
                    {
                        "name": "allowed_users_configured",
                        "expected": True,
                        "actual": bool(allowed),
                    },
                    {
                        "name": "user_is_whitelisted",
                        "expected": True,
                        "actual": False,
                        "user_id": user_id,
                        "normalized_user_id": _normalize_user_id(user_id),
                        "allowed_count": len(allowed),
                    },
                ],
                "detail": detail,
            }
    except Exception:
        # Never break auth enforcement due to logging/telemetry.
        pass

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
