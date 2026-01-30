"""Integration test for YouTube transcript skill.

Tests that the agent can use the youtube-transcript skill to
get transcripts from YouTube videos.

Requires:
- Valid model credentials (Bedrock or OpenAI) in secrets.yml
- yt-dlp installed on the system
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Bypass tool consent prompts for automated testing
os.environ["BYPASS_TOOL_CONSENT"] = "true"

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.skill_service import SkillService


TEST_USER_ID = "test_user_123"
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=kSno1-xOjwI"

# Path to the existing youtube-transcript skill
YOUTUBE_SKILL_SOURCE = Path(__file__).parent.parent.parent / (
    "tools/@splintermaster/youtube-transcript"
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config_with_credentials(temp_dir):
    """Create test configuration with real credentials from secrets.yml."""
    return AgentConfig.from_json_file(
        config_path="config.json",
        secrets_path="secrets.yml",
    )._replace_fields(
        skills_base_dir=temp_dir,
        session_storage_dir=temp_dir,
    )


@pytest.fixture
def config(temp_dir):
    """Create test configuration."""
    # Load from secrets.yml to get real credentials
    try:
        base_config = AgentConfig.from_json_file(
            config_path="config.json",
            secrets_path="secrets.yml",
        )
        # Override paths for testing
        return AgentConfig(
            telegram_bot_token=base_config.telegram_bot_token,
            model_provider=base_config.model_provider,
            bedrock_model_id=base_config.bedrock_model_id,
            bedrock_api_key=base_config.bedrock_api_key,
            openai_api_key=base_config.openai_api_key,
            openai_model_id=base_config.openai_model_id,
            aws_region=base_config.aws_region,
            skills_base_dir=temp_dir,
            session_storage_dir=temp_dir,
        )
    except Exception:
        # Fallback to minimal config for non-credential tests
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


@pytest.fixture
def user_with_youtube_skill(skill_service, temp_dir):
    """Set up a user with the youtube-transcript skill installed."""
    if not YOUTUBE_SKILL_SOURCE.exists():
        pytest.skip(
            f"YouTube skill not found at {YOUTUBE_SKILL_SOURCE}. "
            "Please ensure the skill is installed."
        )

    # Copy skill to user's directory
    user_skills_dir = Path(temp_dir) / TEST_USER_ID
    user_skills_dir.mkdir(parents=True, exist_ok=True)

    dest_skill_dir = user_skills_dir / "youtube-transcript"
    shutil.copytree(YOUTUBE_SKILL_SOURCE, dest_skill_dir)

    return TEST_USER_ID


class TestYouTubeSkillDiscovery:
    """Tests for YouTube skill discovery."""

    def test_skill_is_discovered(
        self, agent_service, user_with_youtube_skill
    ):
        """Test that the youtube-transcript skill is discovered."""
        skills = agent_service._discover_skills(user_with_youtube_skill)

        assert len(skills) >= 1
        skill_names = [s["name"] for s in skills]
        assert "youtube-transcript" in skill_names

    def test_skill_in_system_prompt(
        self, agent_service, user_with_youtube_skill
    ):
        """Test that the skill appears in the system prompt."""
        prompt = agent_service._build_system_prompt(user_with_youtube_skill)

        assert "youtube-transcript" in prompt
        assert "YouTube" in prompt or "transcript" in prompt.lower()
        assert "SKILL.md" in prompt

    def test_skill_metadata_readable(
        self, skill_service, user_with_youtube_skill
    ):
        """Test that skill metadata can be read."""
        metadata = skill_service.get_skill_metadata(
            "youtube-transcript", user_with_youtube_skill
        )

        assert metadata is not None
        assert metadata.get("name") == "youtube-transcript"
        assert "description" in metadata


@pytest.mark.integration
@pytest.mark.slow
class TestYouTubeSkillUsage:
    """Tests for actual YouTube skill usage.

    These tests require valid model credentials from secrets.yml.
    """

    @pytest.mark.asyncio
    async def test_agent_transcribes_video(
        self, agent_service, user_with_youtube_skill
    ):
        """Test that agent can transcribe a YouTube video.

        This test:
        1. Sends a message asking to transcribe a video
        2. Verifies the agent uses the youtube-transcript skill
        3. Checks for actual transcript content in the output
        """
        message = f"Please transcribe this YouTube video: {TEST_VIDEO_URL}"

        response = await agent_service.process_message(
            user_id=user_with_youtube_skill,
            message=message
        )

        # Agent should acknowledge the request
        assert response is not None
        assert len(response) > 0

        # Print response for debugging
        print(f"\n\nAgent response:\n{response}\n\n")

        response_lower = response.lower()

        # Should NOT contain error messages or fallback suggestions
        assert "faiss" not in response_lower, (
            "FAISS error detected - mem0 dependency issue"
        )
        assert "alternative options" not in response_lower, (
            "Agent gave fallback response instead of using skill"
        )
        assert "rev.com" not in response_lower, (
            "Agent suggested external services instead of using skill"
        )

        # Should contain actual transcript content or successful execution
        # The transcript should have actual spoken content, not just metadata
        has_transcript_content = (
            # Check for transcript markers
            len(response) > 500 or  # Real transcripts are substantial
            "00:" in response or  # Timestamp format
            "[" in response and "]" in response  # Subtitle format
        )

        # Or check that yt-dlp was successfully executed
        executed_skill = (
            "yt-dlp" in response_lower and
            "error" not in response_lower
        )

        assert has_transcript_content or executed_skill, (
            f"No transcript content found. Response: {response[:500]}"
        )


class TestSkillFileStructure:
    """Tests for skill file structure validation."""

    def test_skill_has_required_files(self, user_with_youtube_skill, temp_dir):
        """Test that the skill has required SKILL.md file."""
        skill_dir = Path(temp_dir) / user_with_youtube_skill / "youtube-transcript"

        assert skill_dir.exists()
        assert (skill_dir / "SKILL.md").exists()

    def test_skill_md_has_frontmatter(self, user_with_youtube_skill, temp_dir):
        """Test that SKILL.md has valid frontmatter."""
        skill_md = (
            Path(temp_dir) / user_with_youtube_skill /
            "youtube-transcript" / "SKILL.md"
        )

        content = skill_md.read_text()

        assert content.startswith("---")
        assert "name:" in content
        assert "description:" in content

    def test_skill_md_has_instructions(
        self, user_with_youtube_skill, temp_dir
    ):
        """Test that SKILL.md has usage instructions."""
        skill_md = (
            Path(temp_dir) / user_with_youtube_skill /
            "youtube-transcript" / "SKILL.md"
        )

        content = skill_md.read_text()

        # Should have instructions for the agent
        assert "yt-dlp" in content
        assert "bash" in content.lower() or "```" in content
