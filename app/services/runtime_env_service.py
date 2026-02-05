"""Runtime environment variable service.

This service centralizes reads/writes to the process environment.

Why it exists:
- We want services to avoid touching os.environ directly.
- Centralizing env mutation makes it easier to audit, test, and later
  replace with a different mechanism (e.g., per-request env contexts).

Note: This is intentionally small and synchronous.
"""

from __future__ import annotations

import os


class RuntimeEnvService:
    """Small wrapper around os.environ for runtime env mutation."""

    def get(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key, default)

    def set(self, key: str, value: str) -> None:
        os.environ[str(key)] = str(value)

    def unset(self, key: str) -> None:
        os.environ.pop(str(key), None)
