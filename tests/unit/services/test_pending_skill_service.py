"""Unit tests for pending skill onboarding.

These tests ensure pending skills are:
- not discoverable/listed as active skills
- preflighted and onboarded correctly
- marked with FAILED.json on failures
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.pending_skill_service import PendingSkillService
from app.services.skill_service import SkillService


@pytest.fixture
def temp_skills_root():
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _config_for(root: Path) -> AgentConfig:
    return AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=str(root),
        shared_skills_dir=str(root / "shared"),
        session_storage_dir=str(root / "sessions"),
        pending_skills_preflight_enabled=False,
    )


def test_pending_dirs_not_listed_as_skills(temp_skills_root: Path):
    config = _config_for(temp_skills_root)

    # Create a shared pending skill
    shared_pending_skill = temp_skills_root / "shared" / "pending" / "p-skill"
    shared_pending_skill.mkdir(parents=True)
    (shared_pending_skill / "SKILL.md").write_text("---\nname: p-skill\n---\n# pending\n")

    # Create a user pending skill
    user_pending_skill = temp_skills_root / "user1" / "pending" / "u-skill"
    user_pending_skill.mkdir(parents=True)
    (user_pending_skill / "SKILL.md").write_text("---\nname: u-skill\n---\n# pending\n")

    service = SkillService(config)

    # Pending folder should not appear
    assert "pending" not in service.list_shared_skills()
    assert "p-skill" not in service.list_shared_skills()

    skills = service.list_skills("user1")
    assert "pending" not in skills
    assert "u-skill" not in skills


def test_pending_dirs_not_discovered_by_agent_service(temp_skills_root: Path):
    config = _config_for(temp_skills_root)

    # Create an active shared skill
    active_shared = temp_skills_root / "shared" / "active-skill"
    active_shared.mkdir(parents=True)
    (active_shared / "SKILL.md").write_text("---\nname: active-skill\n---\n# ok\n")

    # Create pending shared skill
    shared_pending_skill = temp_skills_root / "shared" / "pending" / "p-skill"
    shared_pending_skill.mkdir(parents=True)
    (shared_pending_skill / "SKILL.md").write_text("---\nname: p-skill\n---\n# pending\n")

    agent_service = AgentService(config)

    discovered = agent_service._discover_skills("user1")
    names = {s["name"] for s in discovered}

    assert "active-skill" in names
    assert "p-skill" not in names


def test_onboard_promotes_user_pending_skill(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    pending_service = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "hello"
    pending_skill.mkdir(parents=True)
    # intentionally provide lowercase skill.md to test normalization
    (pending_skill / "skill.md").write_text("# Hello\n")

    result = pending_service.onboard_pending(user_id="user1", scope="user")
    assert result["onboarded"] == 1
    assert result["failed"] == 0

    active = temp_skills_root / "user1" / "hello"
    assert active.exists()
    assert (active / "SKILL.md").exists()

    # No requirements.txt => no venv should be created
    assert not (active / ".venv").exists()

    # should no longer be under pending
    assert not pending_skill.exists()


def test_onboard_writes_failed_json_on_syntax_error(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    pending_service = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "bad"
    pending_skill.mkdir(parents=True)
    (pending_skill / "SKILL.md").write_text("---\nname: bad\n---\n# Bad\n")
    (pending_skill / "skill.py").write_text("def oops(:\n    pass\n")

    result = pending_service.onboard_pending(user_id="user1", scope="user")
    assert result["onboarded"] == 0
    assert result["failed"] == 1

    # still pending, with FAILED.json
    assert pending_skill.exists()
    assert (pending_skill / "FAILED.json").exists()
