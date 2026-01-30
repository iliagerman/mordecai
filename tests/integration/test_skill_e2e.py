"""End-to-end tests for skill installation and usage.

Tests that skills can be installed from GitHub and used by the agent
to accomplish real tasks.

Note: These tests require:
- Network access to GitHub API
- Valid AWS credentials for Bedrock (or OpenAI API key)
- May hit rate limits if run frequently
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
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

# Test video for YouTube transcript
TEST_YOUTUBE_VIDEO = "https://www.youtube.com/watch?v=kSno1-xOjwI"


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config(temp_dir):
    """Create test configuration."""
    return AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=temp_dir,
        session_storage_dir=temp_dir,
    )


@pytest.fixture
def skill_service(config):
    """Create skill service."""
    return SkillService(config)


@pytest.fixture
def agent_service(config):
    """Create agent service."""
    return AgentService(config)


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
@pytest.mark.e2e
class TestSkillE2E:
    """End-to-end tests for skill usage."""

    @skip_on_rate_limit
    def test_install_pptx_skill_and_verify_structure(
        self, skill_service, temp_dir
    ):
        """Test installing pptx skill and verify it has expected structure."""
        metadata = skill_service.install_skill(PPTX_SKILL_URL)

        assert metadata.name == "pptx"

        skill_dir = Path(temp_dir) / "pptx"
        assert skill_dir.exists()

        # Verify SKILL.md exists and has content
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists()
        content = skill_md.read_text()
        assert "---" in content  # Has frontmatter
        assert "pptx" in content.lower() or "powerpoint" in content.lower()

        # Verify scripts directory exists (pptx skill has scripts/)
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            assert any(scripts_dir.iterdir())

    @skip_on_rate_limit
    def test_install_youtube_skill_and_verify_structure(
        self, skill_service, temp_dir
    ):
        """Test installing youtube-transcript skill."""
        metadata = skill_service.install_skill(YOUTUBE_SKILL_URL)

        assert metadata.name == "youtube-transcript"

        skill_dir = Path(temp_dir) / "youtube-transcript"
        assert skill_dir.exists()

        # Check for SKILL.md or similar documentation
        has_docs = (
            (skill_dir / "SKILL.md").exists() or
            (skill_dir / "README.md").exists() or
            any(skill_dir.glob("*.md"))
        )
        assert has_docs or any(skill_dir.glob("*.py"))

    @skip_on_rate_limit
    def test_agent_system_prompt_includes_installed_skill(
        self, skill_service, agent_service, temp_dir
    ):
        """Test that agent's system prompt includes installed skill."""
        # Install skill
        skill_service.install_skill(PPTX_SKILL_URL)

        # Build system prompt
        prompt = agent_service._build_system_prompt()

        # Should mention the skill
        assert "Installed Skills" in prompt
        assert "pptx" in prompt.lower()
        assert "SKILL.md" in prompt

    @skip_on_rate_limit
    @pytest.mark.slow
    async def test_agent_uses_pptx_skill(
        self, skill_service, agent_service, temp_dir
    ):
        """Test that agent can use the pptx skill to create a presentation.

        This test requires valid model credentials and may incur API costs.
        """
        pytest.skip(
            "Skipping: requires valid model credentials. "
            "Run manually with: pytest -m e2e --run-slow"
        )

        # Install skill
        skill_service.install_skill(PPTX_SKILL_URL)

        # Ask agent to create a presentation
        response = await agent_service.process_message(
            user_id="test-user",
            message=(
                "Create a simple PowerPoint presentation about "
                "Python programming with 3 slides."
            )
        )

        # Agent should acknowledge the task or attempt to use the skill
        assert response is not None
        assert len(response) > 0

    @skip_on_rate_limit
    @pytest.mark.slow
    async def test_agent_uses_youtube_skill(
        self, skill_service, agent_service, temp_dir
    ):
        """Test that agent can use youtube-transcript skill.

        This test requires valid model credentials and may incur API costs.
        """
        pytest.skip(
            "Skipping: requires valid model credentials. "
            "Run manually with: pytest -m e2e --run-slow"
        )

        # Install skill
        skill_service.install_skill(YOUTUBE_SKILL_URL)

        # Ask agent to get transcript
        response = await agent_service.process_message(
            user_id="test-user",
            message=f"Get the transcript from this video: {TEST_YOUTUBE_VIDEO}"
        )

        # Agent should acknowledge or attempt the task
        assert response is not None
        assert len(response) > 0


@pytest.mark.integration
class TestSkillIntegrationWithMocks:
    """Integration tests using mock skills to verify agent behavior."""

    def test_agent_discovers_and_lists_skill_in_prompt(self, temp_dir):
        """Test full flow: create skill -> agent discovers -> in prompt."""
        # Create mock skill
        skill_dir = Path(temp_dir) / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A skill for testing agent integration
---
# Test Skill

## Purpose
This skill helps with testing.

## Instructions
When asked to test something, respond with "Test successful!"
""")

        # Create services
        config = AgentConfig(
            telegram_bot_token="test-token",
            skills_base_dir=temp_dir,
            session_storage_dir=temp_dir,
        )
        agent_service = AgentService(config)

        # Verify discovery
        skills = agent_service._discover_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "test-skill"
        assert "testing" in skills[0]["description"].lower()

        # Verify in system prompt
        prompt = agent_service._build_system_prompt()
        assert "test-skill" in prompt
        assert "testing agent integration" in prompt
        assert "SKILL.md" in prompt

    def test_multiple_skills_all_appear_in_prompt(self, temp_dir):
        """Test that multiple skills all appear in system prompt."""
        # Create multiple mock skills
        for i, (name, desc) in enumerate([
            ("skill-a", "First skill for task A"),
            ("skill-b", "Second skill for task B"),
            ("skill-c", "Third skill for task C"),
        ]):
            skill_dir = Path(temp_dir) / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"""---
name: {name}
description: {desc}
---
# {name}
""")

        config = AgentConfig(
            telegram_bot_token="test-token",
            skills_base_dir=temp_dir,
            session_storage_dir=temp_dir,
        )
        agent_service = AgentService(config)

        prompt = agent_service._build_system_prompt()

        assert "skill-a" in prompt
        assert "skill-b" in prompt
        assert "skill-c" in prompt
        assert "task A" in prompt
        assert "task B" in prompt
        assert "task C" in prompt

    def test_skill_path_allows_agent_to_read_instructions(self, temp_dir):
        """Test that skill path in prompt points to readable SKILL.md."""
        skill_dir = Path(temp_dir) / "readable-skill"
        skill_dir.mkdir()
        skill_md_content = """---
name: readable-skill
description: A skill with detailed instructions
---
# Readable Skill

## Detailed Instructions

1. First, do this
2. Then, do that
3. Finally, complete the task
"""
        (skill_dir / "SKILL.md").write_text(skill_md_content)

        config = AgentConfig(
            telegram_bot_token="test-token",
            skills_base_dir=temp_dir,
            session_storage_dir=temp_dir,
        )
        agent_service = AgentService(config)

        # Get the skill info from discovery
        skills = agent_service._discover_skills()
        skill_path = skills[0]["path"]

        # Verify the path points to a readable SKILL.md
        skill_md_path = Path(skill_path) / "SKILL.md"
        assert skill_md_path.exists()

        content = skill_md_path.read_text()
        assert "Detailed Instructions" in content
        assert "First, do this" in content
