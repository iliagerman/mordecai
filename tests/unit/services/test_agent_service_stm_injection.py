from pathlib import Path
from unittest.mock import MagicMock

from app.config import AgentConfig
from app.services.agent_service import AgentService


def test_build_system_prompt_injects_obsidian_stm(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "me" / "u1").mkdir(parents=True)
    (vault / "me" / "u1" / "stm.md").write_text(
        "# STM\n\n## Session summary: s1\n- created_at: 2026-01-31T00:00:00Z\n\n- Did X\n",
        encoding="utf-8",
    )

    cfg = MagicMock(spec=AgentConfig)
    cfg.skills_base_dir = str(tmp_path / "skills")
    cfg.shared_skills_dir = str(tmp_path / "shared_skills")
    cfg.secrets_path = str(tmp_path / "secrets.yml")
    cfg.obsidian_vault_root = str(vault)
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")

    svc = AgentService(config=cfg, memory_service=None)

    prompt = svc._build_system_prompt(user_id="u1")

    assert "Short-Term Memory (Scratchpad)" in prompt
    assert "## Session summary: s1" in prompt
    assert "Did X" in prompt
