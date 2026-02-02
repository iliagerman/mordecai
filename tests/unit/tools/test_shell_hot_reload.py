from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

import app.config as config_module
from app.config import AgentConfig
from app.tools import shell_env as shell_env_module
from app.tools import skill_secrets as skill_secrets_module


def test_shell_env_context_sets_skills_base_dir_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.delenv("MORDECAI_SKILLS_BASE_DIR", raising=False)

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_base"
    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))

    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    assert os.environ.get("MORDECAI_SKILLS_BASE_DIR") == str(tmp_path / "skills")


def test_shell_inlines_mordecai_skills_base_dir_from_env_template(tmp_path: Path, monkeypatch):
    """If the command references ${MORDECAI_SKILLS_BASE_DIR}, inline it.

    Some shell backends sanitize env and won't pass MORDECAI_SKILLS_BASE_DIR through.
    Inlining avoids failures like trying to read '/<user>/himalaya.toml'.

    This test also covers the fallback where MORDECAI_SKILLS_BASE_DIR exists
    but the shell tool context vars were never set.
    """

    # Ensure this test is not affected by ContextVars set in earlier tests.
    shell_env_module._current_user_id_var.set(None)
    shell_env_module._config_var.set(None)

    monkeypatch.delenv("MORDECAI_SKILLS_BASE_DIR", raising=False)
    monkeypatch.setenv("MORDECAI_SKILLS_BASE_DIR", str(tmp_path / "skills"))

    captured: dict[str, object] = {}

    def _fake_base_shell(**kwargs):
        captured.update(kwargs)
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(
        command='export HIMALAYA_CONFIG="${MORDECAI_SKILLS_BASE_DIR}/u1/himalaya.toml" && himalaya account list',
        timeout_seconds=1,
    )

    assert out.get("stdout") == "ok"
    # Ensure the variable reference was materialized into an absolute path.
    assert str(tmp_path / "skills") in str(captured.get("command"))
    assert "${MORDECAI_SKILLS_BASE_DIR}" not in str(captured.get("command"))


def test_shell_sees_new_secret_without_restart(tmp_path: Path, monkeypatch):
    """Validate hot-reload: after saving secrets, next shell call sees new env without restart."""

    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u1"

    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))

    skill_secrets_module.set_skill_secrets_context(
        user_id=user_id, secrets_path=secrets_path, config=cfg
    )
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

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
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    # Reset runtime tracking to avoid bleed between tests.
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_CONTEXT", None, raising=False)
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_KEYS_BY_SKILL", {}, raising=False)
    monkeypatch.setattr(config_module, "_RUNTIME_SKILL_ENV_MANAGED_KEYS", set(), raising=False)

    call_order: list[str] = []
    refresh_calls: list[dict] = []

    def _fake_refresh(
        *, secrets_path: Path, user_id: str | None = None, skill_names=None, config=None
    ):
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

    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

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


def test_shell_applies_default_timeout_for_himalaya_commands(tmp_path: Path, monkeypatch):
    """Himalaya can hang on network/auth; ensure we apply a default timeout."""

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_timeout"
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    captured: dict[str, object] = {}

    def _fake_base_shell(**kwargs):
        captured.update(kwargs)
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(
        command='HIMALAYA_CONFIG="/tmp/himalaya.toml" himalaya envelope list --output json',
        # Intentionally omit timeout_seconds.
    )

    assert out.get("stdout") == "ok"
    assert captured.get("timeout") == 45


def test_shell_normalizes_backslash_escaped_quotes_for_himalaya(tmp_path: Path, monkeypatch):
    """Models sometimes emit JSON-style escaping (\") in shell strings.

    In bash, `export HIMALAYA_CONFIG=\"/path\"` sets the value to include literal quote
    characters, which then breaks himalaya config discovery.

    Our wrapper should defensively normalize this for himalaya commands.
    """

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_himalaya_quote"
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    captured: dict[str, object] = {}

    def _fake_base_shell(**kwargs):
        captured.update(kwargs)
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(
        command='export HIMALAYA_CONFIG=\\"/tmp/himalaya.toml\\" && himalaya account list',
        timeout_seconds=1,
    )

    assert out.get("stdout") == "ok"
    assert (
        captured.get("command")
        == 'export HIMALAYA_CONFIG="/tmp/himalaya.toml" && himalaya account list'
    )


def test_shell_forces_non_interactive_when_stdin_not_tty(tmp_path: Path, monkeypatch):
    """Prevent hangs: in headless tool execution, force non_interactive=True.

    The upstream strands_tools shell uses an interactive PTY mode that can block
    when stdin isn't a real TTY. Our wrapper should force non-interactive mode
    when stdin is not a TTY, even if the caller/tool schema default is False.
    """

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_non_tty"
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    cfg = AgentConfig(skills_base_dir=str(tmp_path / "skills"))
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    # Simulate a non-interactive/headless environment.
    monkeypatch.setattr(shell_env_module, "_stdin_is_tty", lambda: False)

    captured: dict[str, object] = {}

    def _fake_base_shell(**kwargs):
        captured.update(kwargs)
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(command="echo noop", non_interactive=False)

    assert out.get("stdout") == "ok"
    assert captured.get("non_interactive") is True


def test_shell_clamps_timeout_to_configured_max(tmp_path: Path, monkeypatch):
    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_max_timeout"
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")

    cfg = AgentConfig(
        skills_base_dir=str(tmp_path / "skills"),
        shell_max_timeout_seconds=7,
        shell_progress_heartbeat_seconds=60,
    )
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    captured: dict[str, object] = {}

    def _fake_base_shell(**kwargs):
        captured.update(kwargs)
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(command="echo noop", timeout_seconds=999)
    assert out.get("stdout") == "ok"
    assert captured.get("timeout") == 7


def test_shell_emits_progress_heartbeats_during_long_runs(tmp_path: Path, monkeypatch):
    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_heartbeat"
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")

    cfg = AgentConfig(
        skills_base_dir=str(tmp_path / "skills"),
        shell_progress_heartbeat_seconds=1,
    )
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    events: list[str] = []

    def _fake_mark_progress(event: str | None = None) -> None:
        if event is not None:
            events.append(event)

    def _fake_base_shell(**_kwargs):
        # Run longer than the heartbeat interval so at least one heartbeat fires.
        import time

        time.sleep(1.2)
        return {"stdout": "ok", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "mark_progress", _fake_mark_progress)
    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(command="echo noop", timeout_seconds=5)
    assert out.get("stdout") == "ok"
    assert "tool.shell.start" in events
    assert "tool.shell.end" in events
    assert "tool.shell.heartbeat" in events


def test_shell_stream_output_forces_safe_runner(tmp_path: Path, monkeypatch):
    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    user_id = "u_stream"
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")

    cfg = AgentConfig(
        skills_base_dir=str(tmp_path / "skills"),
        shell_use_safe_runner=False,
        shell_stream_output_enabled=True,
    )
    shell_env_module.set_shell_env_context(user_id=user_id, secrets_path=secrets_path, config=cfg)

    called: dict[str, object] = {"safe": False, "base": False}

    def _fake_safe_shell_run(**kwargs):
        called["safe"] = True
        # Ensure the wrapper passes the stream flag through.
        assert kwargs.get("stream_output") is True
        return {"stdout": "ok", "returncode": 0}

    def _fake_base_shell(**_kwargs):
        called["base"] = True
        return {"stdout": "nope", "returncode": 0}

    monkeypatch.setattr(shell_env_module, "_safe_shell_run", _fake_safe_shell_run)
    monkeypatch.setattr(shell_env_module, "_call_base_shell", _fake_base_shell)

    out = shell_env_module.shell(command="echo noop", timeout_seconds=5)
    assert out.get("stdout") == "ok"
    assert called["safe"] is True
    assert called["base"] is False
