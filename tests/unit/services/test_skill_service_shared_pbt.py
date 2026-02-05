"""Deterministic tests for shared skills functionality.

Replaces noisy Hypothesis/property-based tests with small, deterministic
cases that still validate the same requirements:
- 2.3: Shared skills accessible by all users
- 3.1: Load shared skills for all users
- 3.3: User skills override shared skills
"""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.config import AgentConfig
from app.services.skill_service import SkillService


@contextmanager
def temp_skills_environment():
    """Context manager for creating temporary skills directory."""
    temp_dir = tempfile.mkdtemp()
    try:
        shared_dir = Path(temp_dir) / "shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        yield temp_dir, shared_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def create_skill_service(temp_dir: str, shared_dir: Path) -> SkillService:
    """Create a SkillService with the given directories."""
    config = AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=temp_dir,
        shared_skills_dir=str(shared_dir),
        session_storage_dir=temp_dir,
    )
    return SkillService(config)


def create_skill(base_dir: Path, skill_name: str, content: str = "") -> Path:
    """Helper to create a skill directory with SKILL.md."""
    skill_dir = base_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content or f"---\nname: {skill_name}\n---\n# {skill_name}")
    return skill_dir


def test_shared_skill_visible_to_all_users():
    with temp_skills_environment() as (temp_dir, shared_dir):
        service = create_skill_service(temp_dir, shared_dir)
        skill_name = "shared-skill"
        create_skill(shared_dir, skill_name)

        for user_id in ["u1", "u2", "u3"]:
            assert skill_name in service.list_skills(user_id)


def test_all_shared_skills_listed():
    with temp_skills_environment() as (temp_dir, shared_dir):
        service = create_skill_service(temp_dir, shared_dir)
        skill_names = ["alpha", "beta", "gamma"]
        for name in skill_names:
            create_skill(shared_dir, name)

        user_skills = service.list_skills("u1")
        for name in skill_names:
            assert name in user_skills


def test_shared_skill_path_accessible():
    with temp_skills_environment() as (temp_dir, shared_dir):
        service = create_skill_service(temp_dir, shared_dir)
        skill_name = "shared-skill"
        expected_path = create_skill(shared_dir, skill_name)

        actual_path = service.get_skill_path(skill_name, "u1")
        assert actual_path is not None
        # Use samefile() for cross-platform path comparison (handles macOS /var -> /private/var symlinks)
        assert actual_path.samefile(expected_path)


def test_user_skill_overrides_shared():
    with temp_skills_environment() as (temp_dir, shared_dir):
        service = create_skill_service(temp_dir, shared_dir)
        user_id = "u1"
        skill_name = "overridden-skill"

        create_skill(shared_dir, skill_name, f"---\nname: {skill_name}\n---\nSHARED")

        user_dir = service._get_user_skills_dir(user_id)
        user_skill_path = create_skill(user_dir, skill_name, f"---\nname: {skill_name}\n---\nUSER")

        assert service.get_skill_path(skill_name, user_id) == user_skill_path


def test_override_only_affects_owning_user():
    with temp_skills_environment() as (temp_dir, shared_dir):
        service = create_skill_service(temp_dir, shared_dir)
        user_id = "u1"
        other_user_id = "u2"
        skill_name = "shared-skill"

        shared_skill_path = create_skill(shared_dir, skill_name)

        user_dir = service._get_user_skills_dir(user_id)
        user_skill_path = create_skill(user_dir, skill_name)

        assert service.get_skill_path(skill_name, user_id) == user_skill_path
        # Use samefile() for cross-platform path comparison (handles macOS /var -> /private/var symlinks)
        assert service.get_skill_path(skill_name, other_user_id).samefile(shared_skill_path)


def test_partial_override_preserves_other_shared():
    with temp_skills_environment() as (temp_dir, shared_dir):
        service = create_skill_service(temp_dir, shared_dir)
        user_id = "u1"
        shared_skills = ["alpha", "beta", "gamma"]
        for name in shared_skills:
            create_skill(shared_dir, name)

        override_name = "beta"
        user_dir = service._get_user_skills_dir(user_id)
        create_skill(user_dir, override_name)

        user_skills = service.list_skills(user_id)
        for name in shared_skills:
            assert name in user_skills


class TestSharedSkillsDirectory:
    """Tests for shared skills directory management."""

    def test_shared_skills_directory_created_on_init(self):
        """Test that shared skills directory is created on service init."""
        with temp_skills_environment() as (temp_dir, shared_dir):
            # Remove shared dir to test creation
            shutil.rmtree(shared_dir)
            assert not shared_dir.exists()

            service = create_skill_service(temp_dir, shared_dir)

            assert shared_dir.exists(), "Shared skills dir should be created"
            # Use samefile() for cross-platform path comparison (handles macOS /var -> /private/var symlinks)
            assert Path(service.get_shared_skills_directory()).samefile(shared_dir)

    def test_list_shared_skills_returns_only_shared(self):
        """Test that list_shared_skills only returns shared skills."""
        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create shared skill
            create_skill(shared_dir, "shared-skill")

            # Create user skill
            user_dir = service._get_user_skills_dir("test-user")
            create_skill(user_dir, "user-skill")

            # list_shared_skills should only return shared
            shared_skills = service.list_shared_skills()
            assert "shared-skill" in shared_skills
            assert "user-skill" not in shared_skills
