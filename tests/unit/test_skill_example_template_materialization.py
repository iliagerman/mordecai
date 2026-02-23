from __future__ import annotations

import os
from pathlib import Path

import yaml

from app.config import (
    AgentConfig,
    refresh_runtime_env_from_secrets,
    resolve_user_skills_dir,
)
from app.tools.skill_secrets import set_cached_skill_secrets


def test_example_template_is_materialized_and_exports_skill_config_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")

    cfg = AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=str(tmp_path / "skills"),
        working_folder_base_dir=str(tmp_path / "workspace"),
    )

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

    # Populate the in-memory DB cache (replaces per-user skills_secrets.yml).
    set_cached_skill_secrets({"demo": {"TOKEN": "abc123"}})

    monkeypatch.delenv("DEMO_CONFIG", raising=False)

    try:
        refresh_runtime_env_from_secrets(secrets_path=secrets_path, user_id=user_id, config=cfg)

        # The rendered file is written to workspace/<user>/tmp/, not the skills dir.
        workspace_tmp = tmp_path / "workspace" / user_id / "tmp"
        rendered = workspace_tmp / "demo.toml"
        assert rendered.exists()
        assert rendered.read_text(encoding="utf-8") == 'token = "abc123"\n'

        # The canonical convention: {skill}.toml_example -> export {SKILL}_CONFIG
        assert os.environ.get("DEMO_CONFIG") == str(rendered)

        # A per-user .env convenience file should exist and include only *_CONFIG vars.
        env_path = workspace_tmp / ".env"
        assert env_path.exists()
        env_text = env_path.read_text(encoding="utf-8")
        assert "DEMO_CONFIG=" in env_text
    finally:
        # Clean up module-level cache
        set_cached_skill_secrets({})
