"""Redaction helpers to prevent secret/PII leakage into logs.

Design goals:
- Safe by default: redact obvious secret-like keys and common token formats.
- Minimal surprises: keep structure of dict/list while replacing sensitive values.
- Bounded: truncate large strings/containers to keep logs readable.

NOTE: This is *not* a perfect DLP system. It aims to catch the most common
credential/token cases in agent tool I/O (shell, env vars, headers, etc.).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_REPLACEMENT = "[REDACTED]"
_TRUNC_SUFFIX = "…(truncated)"


# Common secret-ish key names.
_SECRET_KEY_RE = re.compile(
    r"(^|_)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization)($|_)",
    flags=re.IGNORECASE,
)

# Common token formats / sensitive patterns.
_SENSITIVE_VALUE_RES: list[re.Pattern[str]] = [
    # Emails
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    # OpenAI-ish
    re.compile(r"\b(?:sk-|pk-)[A-Za-z0-9]{20,}\b"),
    # AWS access key id
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    # Bearer tokens in headers / logs
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+\b", flags=re.IGNORECASE),
    # Generic 'key=value' patterns
    re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*\S+", flags=re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|apikey)\s*[:=]\s*\S+", flags=re.IGNORECASE),
    re.compile(r"\b(?:token)\s*[:=]\s*\S+", flags=re.IGNORECASE),
    re.compile(r"\b(?:secret|private[_-]?key)\s*[:=]\s*\S+", flags=re.IGNORECASE),
]


def _looks_sensitive_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def redact_text(text: str, *, max_chars: int = 4000) -> str:
    """Redact sensitive substrings in a text blob and truncate."""
    if text is None:
        return text

    out = text
    for rx in _SENSITIVE_VALUE_RES:
        out = rx.sub(_REPLACEMENT, out)

    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + _TRUNC_SUFFIX

    return out


def sanitize(obj: Any, *, max_depth: int = 6, max_chars: int = 4000) -> Any:
    """Sanitize an object for logging.

    - Dict keys that look like secrets are redacted.
    - String values are scanned for sensitive substrings.
    - Deep structures are truncated by depth.
    """

    if max_depth <= 0:
        return "…"

    if obj is None:
        return None

    if isinstance(obj, (int, float, bool)):
        return obj

    if isinstance(obj, bytes):
        # Don't dump binary to logs.
        return f"<bytes:{len(obj)}>"

    if isinstance(obj, str):
        return redact_text(obj, max_chars=max_chars)

    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _looks_sensitive_key(ks):
                out[ks] = _REPLACEMENT
            else:
                out[ks] = sanitize(v, max_depth=max_depth - 1, max_chars=max_chars)
        return out

    # Treat sequences (but not strings/bytes) as lists.
    if isinstance(obj, Sequence):
        # Avoid exploding huge sequences.
        items = list(obj)
        if len(items) > 50:
            items = items[:50]
            items.append("…")
        return [sanitize(v, max_depth=max_depth - 1, max_chars=max_chars) for v in items]

    # Fallback for arbitrary objects.
    try:
        return redact_text(str(obj), max_chars=max_chars)
    except Exception:
        return "<unprintable>"
