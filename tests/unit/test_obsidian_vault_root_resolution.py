"""Unit tests for resolving obsidian_vault_root across environments.

We want deterministic behavior:
- If AGENT_OBSIDIAN_VAULT_ROOT is set, do not override.
- If a container vault root is present and the configured path does not exist
  in this runtime, prefer the container root.
- On mac/local dev, when container root is absent, keep the configured path.

These tests do not hit the network and do not require Docker.
"""

from __future__ import annotations

from pathlib import Path

from app.config import AgentConfig


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
    assert Path(cfg.obsidian_vault_root).resolve() == env_vault.resolve()


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
        # Common host path used in docker-compose; it will not exist in this test runtime.
        obsidian_vault_root="/home/ilia/obsidian-vaults",
    )

    assert cfg.obsidian_vault_root is not None
    assert Path(cfg.obsidian_vault_root).resolve() == container_root.resolve()


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
    assert Path(cfg.obsidian_vault_root).resolve() == existing.resolve()
