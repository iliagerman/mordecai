from pathlib import Path
from unittest.mock import MagicMock

from app.config import AgentConfig
from app.services.agent_service import AgentService


def test_build_system_prompt_includes_missing_skill_env_vars(tmp_path: Path, monkeypatch):
    # Ensure missing env var
    monkeypatch.delenv("HIMALAYA_EMAIL", raising=False)

    # Create a shared himalaya skill with frontmatter requires.env
    shared_dir = tmp_path / "shared_skills"
    (shared_dir / "himalaya").mkdir(parents=True)
    (shared_dir / "himalaya" / "SKILL.md").write_text(
        """---
name: himalaya
description: Email client
requires:
  env:
    - name: HIMALAYA_EMAIL
      prompt: Your email address
      example: user@example.com
---

# Himalaya
""",
        encoding="utf-8",
    )

    cfg = MagicMock(spec=AgentConfig)
    cfg.skills_base_dir = str(tmp_path / "skills")
    cfg.shared_skills_dir = str(shared_dir)
    cfg.secrets_path = str(tmp_path / "secrets.yml")
    cfg.obsidian_vault_root = str(tmp_path / "vault")
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")

    svc = AgentService(config=cfg, memory_service=None)

    prompt = svc._build_system_prompt(user_id="u1")

    assert "Skill Setup Required" in prompt
    assert "set_skill_env_vars" in prompt
    assert "**himalaya**" in prompt
    assert "HIMALAYA_EMAIL" in prompt
