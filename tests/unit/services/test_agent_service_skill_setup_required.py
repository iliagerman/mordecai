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


def test_agent_service_uses_user_skills_dir_template(tmp_path: Path):
    cfg = MagicMock(spec=AgentConfig)
    cfg.skills_base_dir = str(tmp_path / "skills_base")
    cfg.shared_skills_dir = str(tmp_path / "shared")
    cfg.user_skills_dir_template = str(tmp_path / "tenants" / "{username}")

    Path(cfg.shared_skills_dir).mkdir(parents=True, exist_ok=True)

    cfg.secrets_path = str(tmp_path / "secrets.yml")
    cfg.obsidian_vault_root = None
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")

    svc = AgentService(config=cfg, memory_service=None)
    user_dir = svc._get_user_skills_dir("u1")

    assert user_dir == tmp_path / "tenants" / "u1"
    assert user_dir.exists()


def test_build_system_prompt_does_not_require_himalaya_credentials_when_config_file_exists(
    tmp_path: Path,
):
    """Regression test:

    If a per-user Himalaya config file already exists (e.g. created previously),
    the agent should not keep prompting for GMAIL/PASSWORD after starting a new
    session.
    """

    # Create a shared himalaya skill with frontmatter requires.config.
    shared_dir = tmp_path / "shared_skills"
    (shared_dir / "himalaya").mkdir(parents=True)
    (shared_dir / "himalaya" / "SKILL.md").write_text(
        """---
name: himalaya
description: Email client
requires:
  config:
    - name: GMAIL
      prompt: Gmail email address
      example: user@example.com
    - name: PASSWORD
      prompt: Gmail App Password
      example: abcd efgh ijkl mnop
---

# Himalaya
""",
        encoding="utf-8",
    )

    cfg = MagicMock(spec=AgentConfig)
    cfg.skills_base_dir = str(tmp_path / "skills")
    cfg.shared_skills_dir = str(shared_dir)
    cfg.secrets_path = str(tmp_path / "secrets.yml")
    cfg.obsidian_vault_root = None
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")

    svc = AgentService(config=cfg, memory_service=None)

    # Ensure the per-user directory exists and shared skills are mirrored.
    user_dir = svc._get_user_skills_dir("u1")

    # Simulate an already-generated Himalaya config file at the per-user root.
    (user_dir / "himalaya.toml").write_text(
        """[accounts.default]
default = true
email = "user@example.com"
""",
        encoding="utf-8",
    )

    prompt = svc._build_system_prompt(user_id="u1")

    # Skill is installed.
    assert "**himalaya**" in prompt

    # If a config file already exists, we should not keep prompting for credentials.
    assert "Skill Setup Required" not in prompt
