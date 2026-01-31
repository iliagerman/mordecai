"""User whitelist enforcement utilities.

This module centralizes whitelist checks for both HTTP handlers (FastAPI)
and Telegram bot handlers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, status

CONTACT_EMAIL = "iliag@sela.co.il"
DEFAULT_FORBIDDEN_DETAIL = f"contact {CONTACT_EMAIL}"


def _normalize_user_id(user_id: str) -> str:
    # Accept common formats:
    # - Telegram usernames may be provided as "@name" or "name".
    # - Numeric IDs should pass through untouched (aside from strip/lower).
    normalized = (user_id or "").strip().lower()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    return normalized


def normalize_allowed_users(allowed_users: Iterable[str] | None) -> set[str]:
    if not allowed_users:
        return set()
    return {_normalize_user_id(u) for u in allowed_users if _normalize_user_id(u)}


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
