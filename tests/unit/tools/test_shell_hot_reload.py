from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

import app.config as config_module
from app.tools import shell_env as shell_env_module
from app.tools import skill_secrets as skill_secrets_module


def test_shell_sees_new_secret_without_restart(tmp_path: Path, monkeypatch):
    """Validate hot-reload: after saving secrets, next shell call sees new env without restart."""

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u1"

    skill_secrets_module.set_skill_secrets_context(user_id=user_id, secrets_path=secrets_path)
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path)

    # Ensure we start clean.
    os.environ.pop("DEMO_TOKEN", None)

    # Base shell stub returns whatever the process env currently has.
    def _fake_base_shell(**_kwargs):
        return {"stdout": os.environ.get("DEMO_TOKEN", ""), "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    # Initially missing.
    out1 = shell_env_module.shell(command="echo noop")
    assert isinstance(out1, dict)
    assert out1.get("stdout") == ""

    # Simulate onboarding asking user, then persisting env var for this user.
    skill_secrets_module.set_skill_env_vars(
        skill_name="demo-skill",
        env_json=json.dumps({"DEMO_TOKEN": "fresh-secret"}),
        apply_to="user",
    )

    # Next shell call should see it immediately.
    out2 = shell_env_module.shell(command="echo noop")
    assert isinstance(out2, dict)
    assert out2.get("stdout") == "fresh-secret"


def test_shell_calls_refresh_every_invocation_and_before_base_shell(tmp_path: Path, monkeypatch):
    """Ensure the shell tool wrapper always refreshes and does so before executing."""

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_refresh"
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path)

    # Reset runtime tracking to avoid bleed between tests.
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_CONTEXT", None, raising=False)
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_KEYS_BY_SKILL", {}, raising=False)
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_MANAGED_KEYS", set(), raising=False)

    call_order: list[str] = []
    refresh_calls: list[dict] = []

    def _fake_refresh(*, secrets_path: Path, user_id: str | None = None, skill_names=None):
        call_order.append("refresh")
        refresh_calls.append(
            {
                "secrets_path": str(secrets_path),
                "user_id": user_id,
                "skill_names": skill_names,
            }
        )
        return {"ok": True, "applied": 0, "skills": []}

    def _fake_base_shell(**_kwargs):
        call_order.append("base_shell")
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "refresh_runtime_env_from_secrets", _fake_refresh)
    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out1 = shell_env_module.shell(command="echo noop")
    out2 = shell_env_module.shell(command="echo noop")

    assert out1.get("stdout") == "ok"
    assert out2.get("stdout") == "ok"

    assert len(refresh_calls) == 2
    # Refresh must happen before the base shell each time.
    assert call_order == ["refresh", "base_shell", "refresh", "base_shell"]


def test_shell_hot_reloads_from_updated_secrets_file_without_restart(tmp_path: Path, monkeypatch):
    """Prove hot reload reads secrets.yml on each shell call (no tool-based refresh required)."""

    secrets_path = tmp_path / "secrets.yml"
    user_id = "u_disk"

    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path)

    # Reset runtime tracking to avoid bleed between tests.
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_CONTEXT", None, raising=False)
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_KEYS_BY_SKILL", {}, raising=False)
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_MANAGED_KEYS", set(), raising=False)

    os.environ.pop("DISK_TOKEN", None)

    # Start with no env defined in secrets.
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    def _fake_base_shell(**_kwargs):
        # Simulate the actual strands shell reading from the current process env.
        return {"stdout": os.environ.get("DISK_TOKEN", ""), "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out1 = shell_env_module.shell(command="echo noop")
    assert out1.get("stdout") == ""

    # Now mutate secrets.yml on disk (as if an external editor or tool wrote it)
    # without calling set_skill_env_vars.
    secrets_path.write_text(
        yaml.safe_dump(
            {
                "skills": {
                    "demo-skill": {
                        "users": {
                            user_id: {
                                "env": {
                                    "DISK_TOKEN": "disk-secret",
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    out2 = shell_env_module.shell(command="echo noop")
    assert out2.get("stdout") == "disk-secret"
