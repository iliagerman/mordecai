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
        secrets_path=str(root / "secrets.yml"),
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


def test_onboard_blocks_when_required_env_missing(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    pending_service = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "needs-env"
    pending_skill.mkdir(parents=True)
    (pending_skill / "SKILL.md").write_text(
        "---\n"
        "name: needs-env\n"
        "requires:\n"
        "  env:\n"
        "    - name: NEEDS_ENV_TOKEN\n"
        "      prompt: Provide the token for tests\n"
        "---\n\n"
        "# Needs env\n",
        encoding="utf-8",
    )
    (pending_skill / "skill.py").write_text("print('ok')\n", encoding="utf-8")

    result = pending_service.onboard_pending(user_id="user1", scope="user")
    assert result["onboarded"] == 0
    assert result["failed"] == 1

    failed_path = pending_skill / "FAILED.json"
    assert failed_path.exists()
    failed_json = failed_path.read_text(encoding="utf-8")
    assert "validate_required_env" in failed_json
    assert "NEEDS_ENV_TOKEN" in failed_json


def test_onboard_succeeds_after_required_env_set(temp_skills_root: Path):
    from app.config import upsert_skill_env_vars

    config = _config_for(temp_skills_root)
    pending_service = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "needs-env"
    pending_skill.mkdir(parents=True)
    (pending_skill / "SKILL.md").write_text(
        "---\nname: needs-env\nrequires:\n  env:\n    - NEEDS_ENV_TOKEN\n---\n\n# Needs env\n",
        encoding="utf-8",
    )
    (pending_skill / "skill.py").write_text("print('ok')\n", encoding="utf-8")

    # Store the env var for this user under skills.needs-env.users.user1.env
    upsert_skill_env_vars(
        secrets_path=Path(config.secrets_path),
        skill_name="needs-env",
        env_vars={"NEEDS_ENV_TOKEN": "secret"},
        user_id="user1",
    )

    result = pending_service.onboard_pending(user_id="user1", scope="user")
    assert result["failed"] == 0
    assert result["onboarded"] == 1

    active = temp_skills_root / "user1" / "needs-env"
    assert active.exists()


def test_onboard_updates_existing_skill_when_pending_changes(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    pending_service = PendingSkillService(config)

    # Existing active skill
    active = temp_skills_root / "user1" / "hello"
    active.mkdir(parents=True)
    (active / "SKILL.md").write_text("---\nname: hello\n---\n# hello\n", encoding="utf-8")
    (active / "skill.py").write_text("VERSION = 'v1'\n", encoding="utf-8")

    # Updated pending skill with same name
    pending_skill = temp_skills_root / "user1" / "pending" / "hello"
    pending_skill.mkdir(parents=True)
    (pending_skill / "SKILL.md").write_text("---\nname: hello\n---\n# hello\n", encoding="utf-8")
    (pending_skill / "skill.py").write_text("VERSION = 'v2'\n", encoding="utf-8")

    result = pending_service.onboard_pending(user_id="user1", scope="user")
    assert result["failed"] == 0
    assert result["onboarded"] == 1

    assert (active / "skill.py").read_text(encoding="utf-8") == "VERSION = 'v2'\n"

    # Backup should exist under user1/failed
    failed_dir = temp_skills_root / "user1" / "failed"
    assert failed_dir.exists()
    backups = list(failed_dir.glob("hello.*.bak"))
    assert backups, "Expected a backup directory for the replaced skill"
