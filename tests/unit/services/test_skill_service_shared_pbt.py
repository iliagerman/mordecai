"""Property-based tests for shared skills functionality.

Tests the shared skills directory structure and user skill override behavior
using Hypothesis for property-based testing.

Requirements validated:
- 2.3: Shared skills accessible by all users
- 3.1: Load shared skills for all users
- 3.3: User skills override shared skills
"""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app.config import AgentConfig
from app.services.skill_service import SkillService


# Strategy for generating valid user IDs (alphanumeric, reasonable length)
user_id_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"),
    min_size=1,
    max_size=20,
)

# Strategy for generating valid skill names
skill_name_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_"),
    min_size=1,
    max_size=30,
).filter(lambda x: not x.startswith("__") and not x.startswith("-"))


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
    skill_md.write_text(
        content or f"---\nname: {skill_name}\n---\n# {skill_name}"
    )
    return skill_dir


class TestSharedSkillsAccessibility:
    """Property tests for shared skills being accessible to all users.

    Validates Requirement 2.3: Shared skills accessible by all users
    Validates Requirement 3.1: Load shared skills for all users
    """

    @given(
        user_ids=st.lists(user_id_strategy, min_size=2, max_size=5, unique=True),
        skill_name=skill_name_strategy,
    )
    @settings(max_examples=50, deadline=None)
    def test_shared_skill_visible_to_all_users(self, user_ids, skill_name):
        """Property: A shared skill is visible to all users."""
        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create a shared skill
            create_skill(shared_dir, skill_name)

            # Property: Every user should see the shared skill
            for user_id in user_ids:
                skills = service.list_skills(user_id)
                assert skill_name in skills, (
                    f"User {user_id} should see shared skill {skill_name}"
                )

    @given(
        user_id=user_id_strategy,
        skill_names=st.lists(
            skill_name_strategy, min_size=1, max_size=5, unique=True
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_all_shared_skills_listed(self, user_id, skill_names):
        """Property: All shared skills appear in any user's skill list."""
        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create multiple shared skills
            for name in skill_names:
                create_skill(shared_dir, name)

            # Property: User's skill list contains all shared skills
            user_skills = service.list_skills(user_id)
            for name in skill_names:
                assert name in user_skills, (
                    f"Shared skill {name} should be in user's list"
                )

    @given(user_id=user_id_strategy, skill_name=skill_name_strategy)
    @settings(max_examples=50, deadline=None)
    def test_shared_skill_path_accessible(self, user_id, skill_name):
        """Property: Shared skill path is accessible for any user."""
        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create shared skill
            expected_path = create_skill(shared_dir, skill_name)

            # Property: get_skill_path returns the shared skill path
            actual_path = service.get_skill_path(skill_name, user_id)
            assert actual_path is not None, "Shared skill path should be found"
            assert actual_path == expected_path, (
                f"Path should be {expected_path}, got {actual_path}"
            )


class TestUserSkillOverride:
    """Property tests for user skills overriding shared skills.

    Validates Requirement 3.3: User skills override shared skills
    """

    @given(user_id=user_id_strategy, skill_name=skill_name_strategy)
    @settings(max_examples=50, deadline=None)
    def test_user_skill_overrides_shared(self, user_id, skill_name):
        """Property: User skill with same name overrides shared skill."""
        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create shared skill
            create_skill(
                shared_dir, skill_name, f"---\nname: {skill_name}\n---\nSHARED"
            )

            # Create user skill with same name
            user_dir = service._get_user_skills_dir(user_id)
            user_skill_path = create_skill(
                user_dir, skill_name, f"---\nname: {skill_name}\n---\nUSER"
            )

            # Property: get_skill_path returns user's version, not shared
            actual_path = service.get_skill_path(skill_name, user_id)
            assert actual_path == user_skill_path, (
                f"User skill should override shared. "
                f"Expected {user_skill_path}, got {actual_path}"
            )

    @given(
        user_id=user_id_strategy,
        skill_name=skill_name_strategy,
        other_user_id=user_id_strategy,
    )
    @settings(max_examples=50, deadline=None)
    def test_override_only_affects_owning_user(
        self, user_id, skill_name, other_user_id
    ):
        """Property: User override doesn't affect other users."""
        # Skip if same user
        if user_id == other_user_id:
            return

        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create shared skill
            shared_skill_path = create_skill(shared_dir, skill_name)

            # Create user skill override for user_id only
            user_dir = service._get_user_skills_dir(user_id)
            user_skill_path = create_skill(user_dir, skill_name)

            # Property: user_id gets their override
            assert service.get_skill_path(skill_name, user_id) == user_skill_path

            # Property: other_user_id still gets shared version
            other_path = service.get_skill_path(skill_name, other_user_id)
            assert other_path == shared_skill_path

    @given(
        user_id=user_id_strategy,
        shared_skills=st.lists(
            skill_name_strategy, min_size=1, max_size=3, unique=True
        ),
        override_index=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=50, deadline=None)
    def test_partial_override_preserves_other_shared(
        self, user_id, shared_skills, override_index
    ):
        """Property: Overriding one shared skill doesn't hide others."""
        # Ensure override_index is valid
        override_index = override_index % len(shared_skills)

        with temp_skills_environment() as (temp_dir, shared_dir):
            service = create_skill_service(temp_dir, shared_dir)

            # Create all shared skills
            for name in shared_skills:
                create_skill(shared_dir, name)

            # Override just one skill
            override_name = shared_skills[override_index]
            user_dir = service._get_user_skills_dir(user_id)
            create_skill(user_dir, override_name)

            # Property: All skills still visible
            user_skills = service.list_skills(user_id)
            for name in shared_skills:
                assert name in user_skills, (
                    f"Skill {name} should still be visible after override"
                )


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
            assert service.get_shared_skills_directory() == str(shared_dir)

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
