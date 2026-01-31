"""403 Forbidden access logging.

Goal: when we return 403, emit a detailed (but redacted) access log showing:
- request metadata (method/path/query/client ip)
- headers (sanitized)
- captured request body (sanitized, size-bounded)
- the authorization/validation condition(s) that failed (if recorded)

This is intended for debugging authorization/tenant checks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from app.observability.redaction import sanitize

logger = logging.getLogger("mordecai.access")


def _client_ip(request: Request) -> str | None:
    # Prefer X-Forwarded-For if present (common behind proxies).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # XFF can be a comma-separated chain. Take the left-most.
        return xff.split(",")[0].strip() or None

    if request.client:
        return request.client.host

    return None


def _captured_body_for_log(request: Request) -> Any:
    body_bytes: bytes | None = getattr(request.state, "_captured_body", None)
    if body_bytes is None:
        return None

    if not body_bytes:
        return ""

    content_type = request.headers.get("content-type", "")
    truncated = bool(getattr(request.state, "_captured_body_truncated", False))

    # Try JSON first if declared as such.
    if "application/json" in content_type:
        try:
            obj = json.loads(body_bytes.decode("utf-8", errors="replace"))
            out: Any = sanitize(obj)
            if truncated:
                return {"_truncated": True, "_body": out}
            return out
        except Exception:
            # Fall back to text logging.
            pass

    text = body_bytes.decode("utf-8", errors="replace")
    out = sanitize(text)
    if truncated:
        return {"_truncated": True, "_body": out}
    return out


async def log_forbidden_request(request: Request, exc: HTTPException) -> None:
    """Log a redacted, detailed access log line for a 403 response."""
    try:
        failure = getattr(request.state, "authz_failure", None)

        payload: dict[str, Any] = {
            "status_code": 403,
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
            "client_ip": _client_ip(request),
            "headers": sanitize(dict(request.headers)),
            "body": _captured_body_for_log(request),
            "reason": sanitize(failure) if failure is not None else None,
            "detail": sanitize(exc.detail),
        }

        # One-line JSON for easy grepping.
        logger.warning("ACCESS_FORBIDDEN %s", json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        # Never break request handling due to logging.
        logger.exception("Failed to log 403 access")
