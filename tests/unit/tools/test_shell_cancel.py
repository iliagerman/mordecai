from __future__ import annotations

import signal

import pytest

from app.tools import shell_env as shell_env_module


def test_cancel_running_shell_sends_sigterm_and_returns_true(monkeypatch):
    # Arrange: register a fake running pgid
    shell_env_module._RUNNING_SHELL_PGID_BY_USER.clear()
    shell_env_module._RUNNING_SHELL_PGID_BY_USER["u1"] = 12345

    calls: list[tuple[int, int]] = []

    def _fake_killpg(pgid: int, sig: int):
        calls.append((int(pgid), int(sig)))
        # After SIGTERM, the follow-up liveness check uses sig=0.
        if sig == 0:
            raise ProcessLookupError("gone")

    monkeypatch.setattr(shell_env_module.os, "killpg", _fake_killpg)

    # Act
    ok = shell_env_module.cancel_running_shell(user_id="u1")

    # Assert
    assert ok is True
    assert calls[0] == (12345, signal.SIGTERM)
