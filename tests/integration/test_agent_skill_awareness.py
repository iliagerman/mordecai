"""Integration tests for agent skill awareness with per-user skills.

Tests that the agent service correctly discovers installed skills
for each user and includes them in the system prompt.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService, _parse_skill_frontmatter


TEST_USER_ID = "123456789"


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
def agent_service(config):
    """Create agent service."""
    return AgentService(config)


def create_mock_skill(
    skills_base_dir: str, user_id: str, name: str, description: str
) -> Path:
    """Create a mock skill directory for a user."""
    skill_dir = Path(skills_base_dir) / user_id / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"""---
name: {name}
description: {description}
---
# {name}

This is a mock skill for testing.
""")
    return skill_dir


class TestSkillFrontmatterParsing:
    """Tests for SKILL.md frontmatter parsing."""

    def test_parse_simple_frontmatter(self):
        """Test parsing simple YAML frontmatter."""
        content = """---
name: test-skill
description: A test skill for testing
---
# Test Skill
"""
        result = _parse_skill_frontmatter(content)

        assert result["name"] == "test-skill"
        assert result["description"] == "A test skill for testing"

    def test_parse_empty_content(self):
        """Test parsing content without frontmatter."""
        content = "# Just a heading\n\nSome content."
        result = _parse_skill_frontmatter(content)
        assert result == {}


class TestPerUserSkillDiscovery:
    """Tests for per-user skill discovery."""

    def test_discover_skills_empty_directory(self, agent_service):
        """Test discovery with no skills installed."""
        skills = agent_service._discover_skills(TEST_USER_ID)
        assert skills == []

    def test_discover_user_skills(self, agent_service, temp_dir):
        """Test that agent discovers user's skills."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "pptx", "Create PowerPoint presentations"
        )

        skills = agent_service._discover_skills(TEST_USER_ID)

        assert len(skills) == 1
        assert skills[0].name == "pptx"
        assert "PowerPoint" in skills[0].description

    def test_skills_isolated_between_users(self, agent_service, temp_dir):
        """Test that users only see their own skills."""
        create_mock_skill(temp_dir, "user1", "skill-a", "User 1 skill")
        create_mock_skill(temp_dir, "user2", "skill-b", "User 2 skill")

        user1_skills = agent_service._discover_skills("user1")
        user2_skills = agent_service._discover_skills("user2")

        assert len(user1_skills) == 1
        assert user1_skills[0].name == "skill-a"

        assert len(user2_skills) == 1
        assert user2_skills[0].name == "skill-b"

    def test_system_prompt_includes_user_skills(self, agent_service, temp_dir):
        """Test that system prompt includes user's installed skills."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "pptx", "Create presentations"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        assert "Installed Skills" in prompt
        assert "pptx" in prompt
        assert "SKILL.md" in prompt

    def test_system_prompt_without_skills(self, agent_service):
        """Test system prompt when user has no skills."""
        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        assert "Installed Skills" not in prompt
        assert "helpful AI assistant" in prompt

    def test_different_users_get_different_prompts(
        self, agent_service, temp_dir
    ):
        """Test that different users get prompts with their own skills."""
        create_mock_skill(temp_dir, "user1", "skill-a", "Skill A")
        create_mock_skill(temp_dir, "user2", "skill-b", "Skill B")

        prompt1 = agent_service._build_system_prompt("user1")
        prompt2 = agent_service._build_system_prompt("user2")

        assert "skill-a" in prompt1
        assert "skill-b" not in prompt1

        assert "skill-b" in prompt2
        assert "skill-a" not in prompt2


class TestAgentReload:
    """Tests for agent reload after skill changes."""

    def test_discover_skills_after_adding(self, agent_service, temp_dir):
        """Test that new skills are discovered after adding."""
        skills = agent_service._discover_skills(TEST_USER_ID)
        assert len(skills) == 0

        create_mock_skill(temp_dir, TEST_USER_ID, "new-skill", "A new skill")

        skills = agent_service._discover_skills(TEST_USER_ID)
        assert len(skills) == 1
        assert skills[0].name == "new-skill"

    def test_get_user_skills_directory(self, agent_service, temp_dir):
        """Test getting user's skills directory."""
        path = agent_service.get_user_skills_directory(TEST_USER_ID)

        assert TEST_USER_ID in path
        assert Path(path).exists()


class TestSkillInstructionPrompt:
    """Tests for skill instruction guidance in system prompt."""

    def test_system_prompt_instructs_to_read_skill_md(
        self, agent_service, temp_dir
    ):
        """Test that system prompt tells agent to read SKILL.md."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "youtube-transcript",
            "Download YouTube video transcripts"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should have instruction to read SKILL.md
        assert "Read SKILL.md ONCE" in prompt or "SKILL.md" in prompt
        assert "file_read" in prompt

    def test_system_prompt_instructs_to_use_shell(
        self, agent_service, temp_dir
    ):
        """Test that system prompt tells agent to use shell for commands."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "youtube-transcript",
            "Download YouTube video transcripts"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should instruct to use shell tool
        assert "shell" in prompt.lower()
        assert "shell(command=" in prompt or "shell()" in prompt

    def test_system_prompt_warns_not_to_run_skill_as_command(
        self, agent_service, temp_dir
    ):
        """Test that system prompt clarifies skill usage."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "youtube-transcript",
            "Download YouTube video transcripts"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should clarify how to use skills (not as direct commands)
        # The prompt now shows the tool chain: file_read → shell
        assert "shell(command=" in prompt
        assert "→" in prompt  # Shows the flow

    def test_system_prompt_shows_correct_file_read_syntax(
        self, agent_service, temp_dir
    ):
        """Test that system prompt shows correct file_read with mode param."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "test-skill",
            "A test skill"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should show correct syntax with mode="view"
        assert 'mode="view"' in prompt

    def test_system_prompt_warns_not_to_read_multiple_times(
        self, agent_service, temp_dir
    ):
        """Test that system prompt warns against reading same file repeatedly."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "test-skill",
            "A test skill"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should warn to read only once
        assert "ONCE" in prompt

    def test_system_prompt_warns_to_use_shell_after_reading(
        self, agent_service, temp_dir
    ):
        """Test that system prompt tells agent to use shell after reading."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "test-skill",
            "A test skill"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should indicate shell comes after file_read
        assert "shell()" in prompt or "→ shell" in prompt

    def test_system_prompt_lists_available_tools(
        self, agent_service, temp_dir
    ):
        """Test that system prompt lists available tools."""
        create_mock_skill(
            temp_dir, TEST_USER_ID, "test-skill",
            "A test skill"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should list the tools
        assert "shell(command=" in prompt
        assert "file_read(path=" in prompt

    def test_skill_path_in_prompt_points_to_skill_md(
        self, agent_service, temp_dir
    ):
        """Test that skill path in prompt correctly points to SKILL.md."""
        skill_dir = create_mock_skill(
            temp_dir, TEST_USER_ID, "my-skill",
            "My test skill"
        )

        prompt = agent_service._build_system_prompt(TEST_USER_ID)

        # Should have correct path to SKILL.md
        expected_path = f"{skill_dir}/SKILL.md"
        assert expected_path in prompt
