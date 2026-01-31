import json
from pathlib import Path

import yaml

from app.config import AgentConfig, resolve_user_skills_secrets_path
from app.tools.skill_secrets import set_skill_config, set_skill_secrets_context


def test_set_skill_config_persists_to_secrets_and_materializes_file(tmp_path: Path):
    secrets_path = tmp_path / "secrets.yml"
    config_path = tmp_path / "himalaya" / "config.toml"

    # Minimal config for per-user skill secrets path resolution.
    cfg = AgentConfig(telegram_bot_token="test-token", skills_base_dir=str(tmp_path / "skills"))

    set_skill_secrets_context(user_id="u1", secrets_path=secrets_path, config=cfg)

    cfg = {
        "path": str(config_path),
        "email": "user@example.com",
        "display_name": "Test User",
        "imap": {
            "host": "imap.example.com",
            "port": 993,
            "login": "user@example.com",
            "password": "pw-should-not-appear-in-tool-result",
        },
    }

    result = set_skill_config(
        skill_name="himalaya",
        config_json=json.dumps(cfg),
        apply_to="user",
    )

    # Tool output should never echo values.
    assert "pw-should-not-appear-in-tool-result" not in result
    assert "user@example.com" not in result

    user_secrets_path = resolve_user_skills_secrets_path(cfg, "u1")
    data = yaml.safe_load(user_secrets_path.read_text(encoding="utf-8"))

    assert data["skills"]["himalaya"]["path"] == str(config_path)
    assert data["skills"]["himalaya"]["email"] == "user@example.com"

    # refresh_runtime_env_from_secrets should have best-effort materialized the file.
    assert config_path.exists()
    content = config_path.read_text(encoding="utf-8")
    assert "user@example.com" in content
