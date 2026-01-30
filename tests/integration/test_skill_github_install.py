"""Integration tests for GitHub skill installation.

Tests that skills can be installed from GitHub repository URLs
and that the agent becomes aware of them.

Note: These tests make real HTTP requests to GitHub API.
They may fail due to rate limiting (403 errors) when run frequently.
Use pytest -m "not integration" to skip these tests.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.services.skill_service import SkillInstallError, SkillService


# Real GitHub URLs for testing
PPTX_SKILL_URL = (
    "https://github.com/aws-samples/"
    "sample-strands-agents-agentskills/tree/main/skills/pptx"
)
YOUTUBE_SKILL_URL = (
    "https://github.com/michalparkola/"
    "tapestry-skills-for-claude-code/tree/main/youtube-transcript"
)


@pytest.fixture
def temp_skills_dir():
    """Create a temporary directory for skills."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def skill_service(temp_skills_dir):
    """Create a SkillService with temporary skills directory."""
    config = AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=temp_skills_dir,
        session_storage_dir=temp_skills_dir,
    )
    return SkillService(config)


def skip_on_rate_limit(func):
    """Decorator to skip test if GitHub rate limit is hit."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SkillInstallError as e:
            if "rate limit" in str(e).lower():
                pytest.skip("GitHub API rate limit exceeded")
            raise
    return wrapper


@pytest.mark.integration
class TestGitHubSkillInstallation:
    """Tests for installing skills from GitHub URLs."""

    @skip_on_rate_limit
    def test_install_pptx_skill_from_github(
        self, skill_service, temp_skills_dir
    ):
        """Test installing the pptx skill from GitHub.

        This skill has nested directories (ooxml/, scripts/) and
        multiple markdown files.
        """
        metadata = skill_service.install_skill(PPTX_SKILL_URL)

        assert metadata.name == "pptx"
        assert metadata.source_url == PPTX_SKILL_URL
        assert metadata.installed_at is not None

        skill_dir = Path(temp_skills_dir) / "pptx"
        assert skill_dir.exists()
        assert skill_dir.is_dir()

        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists()

        skills = skill_service.list_skills()
        assert "pptx" in skills

    @skip_on_rate_limit
    def test_install_youtube_transcript_skill(
        self, skill_service, temp_skills_dir
    ):
        """Test installing the youtube-transcript skill from GitHub."""
        metadata = skill_service.install_skill(YOUTUBE_SKILL_URL)

        assert metadata.name == "youtube-transcript"
        assert metadata.source_url == YOUTUBE_SKILL_URL

        skill_dir = Path(temp_skills_dir) / "youtube-transcript"
        assert skill_dir.exists()

        skills = skill_service.list_skills()
        assert "youtube-transcript" in skills

    @skip_on_rate_limit
    def test_skill_has_skill_md_content(
        self, skill_service, temp_skills_dir
    ):
        """Test that installed skill has readable SKILL.md."""
        skill_service.install_skill(PPTX_SKILL_URL)

        skill_md = Path(temp_skills_dir) / "pptx" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")

        assert content.startswith("---")
        assert "name:" in content or "description:" in content

    @skip_on_rate_limit
    def test_uninstall_github_skill(self, skill_service, temp_skills_dir):
        """Test uninstalling a skill installed from GitHub."""
        skill_service.install_skill(PPTX_SKILL_URL)
        assert skill_service.skill_exists("pptx")

        result = skill_service.uninstall_skill("pptx")
        assert "uninstalled" in result.lower()

        assert not skill_service.skill_exists("pptx")
        skill_dir = Path(temp_skills_dir) / "pptx"
        assert not skill_dir.exists()

    @skip_on_rate_limit
    def test_reinstall_skill_overwrites(
        self, skill_service, temp_skills_dir
    ):
        """Test that reinstalling a skill overwrites existing files."""
        skill_service.install_skill(PPTX_SKILL_URL)
        metadata2 = skill_service.install_skill(PPTX_SKILL_URL)

        assert metadata2.name == "pptx"
        assert skill_service.skill_exists("pptx")

    @skip_on_rate_limit
    def test_list_multiple_skills(self, skill_service, temp_skills_dir):
        """Test listing multiple installed skills."""
        skill_service.install_skill(PPTX_SKILL_URL)
        skill_service.install_skill(YOUTUBE_SKILL_URL)

        skills = skill_service.list_skills()

        assert len(skills) >= 2
        assert "pptx" in skills
        assert "youtube-transcript" in skills


@pytest.mark.integration
class TestSkillDiscovery:
    """Tests for skill discovery and agent awareness."""

    @skip_on_rate_limit
    def test_discover_installed_skill_metadata(
        self, skill_service, temp_skills_dir
    ):
        """Test that skill metadata can be read after installation."""
        skill_service.install_skill(PPTX_SKILL_URL)

        metadata = skill_service.get_skill_metadata("pptx")

        assert metadata is not None
        assert "name" in metadata or "description" in metadata
