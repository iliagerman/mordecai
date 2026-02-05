"""Unit tests for resolving obsidian_vault_root across environments.

We want deterministic behavior:
- If AGENT_OBSIDIAN_VAULT_ROOT is set, do not override.
- If a container vault root is present and the configured path does not exist
  in this runtime, prefer the container root.
- On mac/local dev, when container root is absent, keep the configured path.

These tests do not hit the network and do not require Docker.

NOTE: The container-specific vault root resolution behavior was removed in favor of
always using the repo-local scratchpad. These tests are skipped until/unless the
feature is re-implemented.
"""

from __future__ import annotations

from pathlib import Path  # noqa: F401 - needed for skipped tests

import pytest

from app.config import AgentConfig


@pytest.mark.skip(reason="Container-specific vault root resolution is no longer implemented")
def test_obsidian_vault_root_env_override_wins(tmp_path, monkeypatch):
    container_root = tmp_path / "container_vault"
    container_root.mkdir(parents=True)

    # Even if a container root exists and obsidian_vault_root is missing, the env wins.
    env_vault = tmp_path / "env_vault"
    env_vault.mkdir(parents=True)
    monkeypatch.setenv("AGENT_OBSIDIAN_VAULT_ROOT", str(env_vault))

    # Point the container default to our temp directory.
    monkeypatch.setattr(
        "app.config.DEFAULT_CONTAINER_OBSIDIAN_VAULT_ROOT",
        str(container_root),
        raising=True,
    )

    # Do not pass obsidian_vault_root explicitly; allow BaseSettings env loading.
    cfg = AgentConfig(telegram_bot_token="test-token")
    assert cfg.obsidian_vault_root is not None
    assert cfg.obsidian_vault_root == env_vault.resolve()


@pytest.mark.skip(reason="Container-specific vault root resolution is no longer implemented")
def test_container_root_is_used_when_configured_path_missing(tmp_path, monkeypatch):
    container_root = tmp_path / "container_vault"
    container_root.mkdir(parents=True)

    monkeypatch.delenv("AGENT_OBSIDIAN_VAULT_ROOT", raising=False)
    monkeypatch.setattr(
        "app.config.DEFAULT_CONTAINER_OBSIDIAN_VAULT_ROOT",
        str(container_root),
        raising=True,
    )

    cfg = AgentConfig(
        telegram_bot_token="test-token",
        # Use a path that will not exist in this test runtime.
        # (Historically this pointed at an external Obsidian vault; this deployment
        # uses the repo-local scratchpad instead.)
        obsidian_vault_root="/nonexistent/scratchpad",
    )

    assert cfg.obsidian_vault_root is not None
    assert cfg.obsidian_vault_root == container_root.resolve()


@pytest.mark.skip(reason="Container-specific vault root resolution is no longer implemented")
def test_configured_path_is_kept_when_it_exists(tmp_path, monkeypatch):
    existing = tmp_path / "my_vault"
    existing.mkdir(parents=True)

    # Also have a container root present; we should *not* override because configured exists.
    container_root = tmp_path / "container_vault"
    container_root.mkdir(parents=True)

    monkeypatch.delenv("AGENT_OBSIDIAN_VAULT_ROOT", raising=False)
    monkeypatch.setattr(
        "app.config.DEFAULT_CONTAINER_OBSIDIAN_VAULT_ROOT",
        str(container_root),
        raising=True,
    )

    cfg = AgentConfig(telegram_bot_token="test-token", obsidian_vault_root=str(existing))
    assert cfg.obsidian_vault_root is not None
    assert cfg.obsidian_vault_root == existing.resolve()
