"""Unit tests for AgentService shared skill sync.

These tests validate that shared skills are mirrored into the per-user skills
folder on each agent creation/message, with overwrite semantics.

We test at the filesystem level (no Strands agent execution required).
"""

import shutil
from pathlib import Path

from app.config import AgentConfig
from app.services.agent_service import AgentService


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_shared_skills_synced_with_overwrite_and_deletion(tmp_path):
    skills_base = tmp_path / "skills"

    # shared_skills_dir will be derived to {skills_base}/shared by AgentConfig
    shared_dir = skills_base / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)

    # Shared directory skill
    shared_skill_dir = shared_dir / "hello-skill"
    (shared_skill_dir).mkdir(parents=True, exist_ok=True)
    _write(shared_skill_dir / "skill.py", "VERSION = 'v1'\n")

    # Shared file skill
    _write(shared_dir / "util_skill.py", "VALUE = 1\n")

    # Reserved directories should never sync
    (shared_dir / "pending").mkdir(parents=True, exist_ok=True)

    config = AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=str(skills_base),
    )
    service = AgentService(config)

    user_dir = service._get_user_skills_dir("u1")

    # Initial sync
    assert (user_dir / "hello-skill" / "skill.py").read_text(encoding="utf-8") == "VERSION = 'v1'\n"
    assert (user_dir / "util_skill.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (user_dir / "pending").exists()

    # Overwrite update
    _write(shared_skill_dir / "skill.py", "VERSION = 'v2'\n")
    _write(shared_dir / "util_skill.py", "VALUE = 2\n")

    # Sync again (simulates the next message/agent creation)
    service._sync_shared_skills(user_dir)

    assert (user_dir / "hello-skill" / "skill.py").read_text(encoding="utf-8") == "VERSION = 'v2'\n"
    assert (user_dir / "util_skill.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    # Deletion: removing a shared skill should remove the previously-synced copy
    shutil.rmtree(shared_skill_dir)
    service._sync_shared_skills(user_dir)

    assert not (user_dir / "hello-skill").exists()
