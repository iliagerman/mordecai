from __future__ import annotations

import os
from pathlib import Path

import yaml

from app.config import (
    AgentConfig,
    refresh_runtime_env_from_secrets,
    resolve_user_skills_dir,
    resolve_user_skills_secrets_path,
)


def test_example_template_is_materialized_and_exports_skill_config_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")

    cfg = AgentConfig(telegram_bot_token="test-token", skills_base_dir=str(tmp_path / "skills"))

    # Create a user skill folder with an *_example template.
    user_id = "user1"
    user_dir = resolve_user_skills_dir(cfg, user_id, create=True)
    skill_dir = user_dir / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)

    tpl = skill_dir / "demo.toml_example"
    tpl.write_text('token = "[TOKEN]"\n', encoding="utf-8")

    # Global secrets.yml needs to list the skill so refresh iterates over it.
    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text(yaml.safe_dump({"skills": {"demo": {}}}), encoding="utf-8")

    # Per-user skills_secrets.yml provides the placeholder values.
    user_secrets_path = resolve_user_skills_secrets_path(cfg, user_id)
    user_secrets_path.write_text(
        yaml.safe_dump({"skills": {"demo": {"TOKEN": "abc123"}}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("DEMO_CONFIG", raising=False)

    refresh_runtime_env_from_secrets(secrets_path=secrets_path, user_id=user_id, config=cfg)

    rendered = skill_dir / "demo.toml"
    assert rendered.exists()
    assert rendered.read_text(encoding="utf-8") == 'token = "abc123"\n'

    # The canonical convention: {skill}.toml_example -> export {SKILL}_CONFIG
    assert os.environ.get("DEMO_CONFIG") == str(rendered)

    # A per-user .env convenience file should exist and include only *_CONFIG vars.
    env_path = user_dir / ".env"
    assert env_path.exists()
    env_text = env_path.read_text(encoding="utf-8")
    assert "DEMO_CONFIG=" in env_text
