"""Unit tests for per-user skill service.

Tests skill installation, uninstallation, and GitHub URL parsing
with mocked HTTP requests to avoid rate limiting.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import AgentConfig
from app.services.skill_service import (
    SkillInstallError,
    SkillNotFoundError,
    SkillService,
)


TEST_USER_ID = "testuser"


@pytest.fixture
def temp_skills_dir():
    """Create a temporary directory for skills."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_shared_dir(temp_skills_dir):
    """Create a temporary shared skills directory."""
    shared_dir = Path(temp_skills_dir) / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    return str(shared_dir)


@pytest.fixture
def skill_service(temp_skills_dir, temp_shared_dir):
    """Create a SkillService with temporary skills directory."""
    config = AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=temp_skills_dir,
        shared_skills_dir=temp_shared_dir,
        session_storage_dir=temp_skills_dir,
    )
    return SkillService(config)


class TestGitHubUrlParsing:
    """Tests for GitHub URL parsing."""

    def test_parse_valid_github_tree_url(self, skill_service):
        """Test parsing a valid GitHub tree URL."""
        url = (
            "https://github.com/aws-samples/sample-strands-agents-agentskills/tree/main/skills/pptx"
        )
        result = skill_service._parse_github_url(url)

        assert result is not None
        owner, repo, branch, path = result
        assert owner == "aws-samples"
        assert repo == "sample-strands-agents-agentskills"
        assert branch == "main"
        assert path == "skills/pptx"

    def test_parse_github_url_with_different_branch(self, skill_service):
        """Test parsing GitHub URL with non-main branch."""
        url = "https://github.com/user/repo/tree/develop/src/skill"
        result = skill_service._parse_github_url(url)

        assert result is not None
        owner, repo, branch, path = result
        assert branch == "develop"
        assert path == "src/skill"

    def test_parse_non_github_url_returns_none(self, skill_service):
        """Test that non-GitHub URLs return None."""
        url = "https://example.com/some/path"
        result = skill_service._parse_github_url(url)
        assert result is None


class TestPerUserSkillInstallation:
    """Tests for per-user skill installation."""

    def test_user_skills_directory_created(self, skill_service, temp_skills_dir):
        """Test that user-specific skills directory is created."""
        user_dir = skill_service._get_user_skills_dir(TEST_USER_ID)

        assert user_dir.exists()
        assert user_dir.is_dir()
        # Use samefile() for cross-platform path comparison (handles macOS /var -> /private/var symlinks)
        assert user_dir.samefile(Path(temp_skills_dir) / TEST_USER_ID)

    def test_user_skills_directory_uses_template_when_set(self, temp_skills_dir, temp_shared_dir):
        """If user_skills_dir_template is set, it overrides skills_base_dir/<user_id>."""
        config = AgentConfig(
            telegram_bot_token="test-token",
            skills_base_dir=temp_skills_dir,
            shared_skills_dir=temp_shared_dir,
            session_storage_dir=temp_skills_dir,
            user_skills_dir_template=str(Path(temp_skills_dir) / "per-user" / "{username}"),
        )
        svc = SkillService(config)

        user_dir = svc._get_user_skills_dir(TEST_USER_ID)
        # Use samefile() for cross-platform path comparison (handles macOS /var -> /private/var symlinks)
        assert user_dir.samefile(Path(temp_skills_dir) / "per-user" / TEST_USER_ID)

        pending_dir = svc._get_user_pending_skills_dir(TEST_USER_ID)
        assert pending_dir == user_dir / "pending"

    def test_different_users_have_separate_directories(self, skill_service, temp_skills_dir):
        """Test that different users get separate skill directories."""
        user1_dir = skill_service._get_user_skills_dir("user1")
        user2_dir = skill_service._get_user_skills_dir("user2")

        assert user1_dir != user2_dir
        assert user1_dir.name == "user1"
        assert user2_dir.name == "user2"

    def test_install_skill_from_github_success(self, skill_service, temp_skills_dir):
        """Test successful skill installation from GitHub."""
        url = "https://github.com/owner/repo/tree/main/skills/test-skill"

        api_response = [
            {
                "name": "SKILL.md",
                "type": "file",
                "path": "skills/test-skill/SKILL.md",
                "download_url": "https://raw.githubusercontent.com/...",
            },
        ]

        skill_md_content = b"""---
name: test-skill
description: A test skill
---
# Test Skill
"""

        def mock_urlopen(req, timeout=None):
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(api_response).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        def mock_urlretrieve(url, dest):
            Path(dest).write_bytes(skill_md_content)

        with patch("urllib.request.urlopen", mock_urlopen):
            with patch("urllib.request.urlretrieve", mock_urlretrieve):
                metadata = skill_service.install_skill(url, TEST_USER_ID)

        assert metadata.name == "test-skill"
        assert metadata.source_url == url

        # Verify installed in user's directory
        skill_dir = Path(temp_skills_dir) / TEST_USER_ID / "test-skill"
        assert skill_dir.exists()

    def test_skills_isolated_between_users(self, skill_service, temp_skills_dir):
        """Test that skills are isolated between users."""
        # Create skill for user1
        user1_dir = skill_service._get_user_skills_dir("user1")
        skill_dir = user1_dir / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n")

        # User1 should see the skill
        assert skill_service.skill_exists("my-skill", "user1")

        # User2 should NOT see the skill
        assert not skill_service.skill_exists("my-skill", "user2")


class TestSkillUninstallation:
    """Tests for per-user skill uninstallation."""

    def test_uninstall_user_skill(self, skill_service, temp_skills_dir):
        """Test uninstalling a user's skill."""
        user_dir = skill_service._get_user_skills_dir(TEST_USER_ID)
        skill_dir = user_dir / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        result = skill_service.uninstall_skill("my-skill", TEST_USER_ID)

        assert "uninstalled" in result.lower()
        assert not skill_dir.exists()

    def test_uninstall_nonexistent_skill_raises(self, skill_service):
        """Test that uninstalling nonexistent skill raises error."""
        with pytest.raises(SkillNotFoundError):
            skill_service.uninstall_skill("nonexistent", TEST_USER_ID)

    def test_uninstall_does_not_affect_other_users(self, skill_service, temp_skills_dir):
        """Test that uninstalling doesn't affect other users' skills."""
        # Create same skill for two users
        for user_id in ["user1", "user2"]:
            user_dir = skill_service._get_user_skills_dir(user_id)
            skill_dir = user_dir / "shared-name"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Skill")

        # Uninstall for user1
        skill_service.uninstall_skill("shared-name", "user1")

        # User1's skill should be gone
        assert not skill_service.skill_exists("shared-name", "user1")

        # User2's skill should still exist
        assert skill_service.skill_exists("shared-name", "user2")


class TestSkillListing:
    """Tests for per-user skill listing."""

    def test_list_empty_user_skills(self, skill_service):
        """Test listing skills for user with no user-specific skills.

        Note: list_skills returns shared + user skills, so we check
        that no user-specific skills are added beyond shared ones.
        """
        # Get baseline shared skills count
        shared_skills = skill_service.list_shared_skills()
        skills = skill_service.list_skills(TEST_USER_ID)
        # User should only see shared skills (no user-specific ones)
        assert set(skills) == set(shared_skills)

    def test_list_user_skills(self, skill_service, temp_skills_dir):
        """Test listing a user's installed skills."""
        user_dir = skill_service._get_user_skills_dir(TEST_USER_ID)

        # Get baseline shared skills
        shared_skills = set(skill_service.list_shared_skills())

        # Create two user skills
        for name in ["skill-a", "skill-b"]:
            skill_dir = user_dir / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {name}")

        skills = skill_service.list_skills(TEST_USER_ID)

        # Should have shared skills + 2 user skills
        assert len(skills) == len(shared_skills) + 2
        assert "skill-a" in skills
        assert "skill-b" in skills

    def test_list_skills_only_shows_user_skills(self, skill_service, temp_skills_dir):
        """Test that list_skills only shows the user's own skills."""
        # Create skill for user1
        user1_dir = skill_service._get_user_skills_dir("user1")
        (user1_dir / "user1-skill").mkdir(parents=True)
        (user1_dir / "user1-skill" / "SKILL.md").write_text("# Skill")

        # Create skill for user2
        user2_dir = skill_service._get_user_skills_dir("user2")
        (user2_dir / "user2-skill").mkdir(parents=True)
        (user2_dir / "user2-skill" / "SKILL.md").write_text("# Skill")

        # User1 should only see their skill
        user1_skills = skill_service.list_skills("user1")
        assert "user1-skill" in user1_skills
        assert "user2-skill" not in user1_skills

        # User2 should only see their skill
        user2_skills = skill_service.list_skills("user2")
        assert "user2-skill" in user2_skills
        assert "user1-skill" not in user2_skills


class TestSkillMetadata:
    """Tests for per-user skill metadata reading."""

    def test_get_skill_metadata(self, skill_service, temp_skills_dir):
        """Test reading skill metadata for a user."""
        user_dir = skill_service._get_user_skills_dir(TEST_USER_ID)
        skill_dir = user_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill for testing
---
# Test Skill
""")

        metadata = skill_service.get_skill_metadata("test-skill", TEST_USER_ID)

        assert metadata is not None
        assert metadata.get("name") == "test-skill"

    def test_get_user_skills_directory(self, skill_service, temp_skills_dir):
        """Test getting user's skills directory path."""
        path = skill_service.get_user_skills_directory(TEST_USER_ID)

        assert TEST_USER_ID in path
        assert Path(path).exists()
