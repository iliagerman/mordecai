"""Unit tests for SystemPromptBuilder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.agent.prompt_builder import SystemPromptBuilder
from app.services.agent.skills import SkillRepository


@pytest.fixture
def mock_config() -> MagicMock:
    """Create a mock config."""
    config = MagicMock()
    config.timezone = "UTC"
    config.memory_enabled = False
    config.personality_enabled = False
    config.obsidian_vault_root = None
    config.personality_max_chars = 20000
    config.agent_commands = None
    return config


@pytest.fixture
def mock_skill_repo() -> SkillRepository:
    """Create a mock skill repository."""
    return MagicMock(spec=SkillRepository)


@pytest.fixture
def builder(mock_config: MagicMock, mock_skill_repo: SkillRepository) -> SystemPromptBuilder:
    """Create a SystemPromptBuilder."""
    personality_service = MagicMock()
    personality_service.is_enabled.return_value = False
    personality_service.load.return_value = {}

    return SystemPromptBuilder(
        config=mock_config,
        skill_repo=mock_skill_repo,
        personality_service=personality_service,
        working_dir_resolver=lambda user_id: Path(f"/work/{user_id}"),
        obsidian_stm_cache={},
        user_agent_names={},
        has_cron=False,
    )


def test_onboarding_section_with_full_content(builder: SystemPromptBuilder) -> None:
    """Test _onboarding_section generates correct prompt with soul and id."""
    soul = "You are a helpful assistant."
    id_content = "Name: TestBot"

    context = {"soul": soul, "id": id_content}
    result = builder._onboarding_section(context)

    assert "Welcome - First Interaction" in result
    assert "This is the user's first interaction with you!" in result
    assert (
        "IMPORTANT: Tell the user that you've set up their personalized personality files" in result
    )
    assert "SHOW them the content of these files" in result
    assert "### Your Personality (soul.md)" in result
    assert soul in result
    assert "### Your Identity (id.md)" in result
    assert id_content in result


def test_onboarding_section_with_soul_only(builder: SystemPromptBuilder) -> None:
    """Test _onboarding_section with only soul content."""
    context = {"soul": "Be friendly!", "id": None}
    result = builder._onboarding_section(context)

    assert "### Your Personality (soul.md)" in result
    assert "Be friendly!" in result
    # id section should not be included when None
    assert "### Your Identity (id.md)" not in result


def test_onboarding_section_with_id_only(builder: SystemPromptBuilder) -> None:
    """Test _onboarding_section with only id content."""
    context = {"soul": None, "id": "Name: Bot"}
    result = builder._onboarding_section(context)

    assert "### Your Identity (id.md)" in result
    assert "Name: Bot" in result
    # soul section should not be included when None
    assert "### Your Personality (soul.md)" not in result


def test_onboarding_section_with_empty_context(builder: SystemPromptBuilder) -> None:
    """Test _onboarding_section with empty context."""
    context = {"soul": None, "id": None}
    result = builder._onboarding_section(context)

    # Should still have the header and instruction
    assert "Welcome - First Interaction" in result
    assert (
        "IMPORTANT: Tell the user that you've set up their personalized personality files" in result
    )
    # But no file sections
    assert "### Your Personality (soul.md)" not in result
    assert "### Your Identity (id.md)" not in result


def test_build_with_onboarding_context(builder: SystemPromptBuilder) -> None:
    """Test that build() includes onboarding section when context is provided."""
    soul = "Be helpful!"
    id_content = "Name: Mordecai"
    onboarding_context = {"soul": soul, "id": id_content}

    result = builder.build(
        user_id="testuser",
        onboarding_context=onboarding_context,
    )

    # Should include onboarding section
    assert "Welcome - First Interaction" in result
    assert "Be helpful!" in result
    assert "Name: Mordecai" in result


def test_build_without_onboarding_context(builder: SystemPromptBuilder) -> None:
    """Test that build() excludes onboarding section when no context."""
    result = builder.build(user_id="testuser")

    # Should NOT include onboarding section
    assert "Welcome - First Interaction" not in result
    assert (
        "IMPORTANT: Tell the user that you've set up their personalized personality files"
        not in result
    )


def test_onboarding_section_when_deterministic_onboarding_already_sent(
    builder: SystemPromptBuilder,
) -> None:
    """If the transport already sent onboarding content, do not repeat it in the prompt."""
    context = {"_onboarding_deterministic_sent": "true"}
    result = builder._onboarding_section(context)

    assert "Onboarding (Already Sent)" in result
    assert "Do NOT repeat" in result
    assert "Do not end your message with a question" in result


def test_prompt_includes_skill_shell_quoting_and_verification_guardrails(
    builder: SystemPromptBuilder,
    mock_skill_repo: SkillRepository,
) -> None:
    """Ensure the prompt warns against unverified claims and bad shell escaping.

    This is a regression guard for a common failure mode:
    the model emits `\"...\"` sequences and then tools see literal quote chars.
    """

    skill = MagicMock()
    skill.name = "himalaya"
    skill.description = "email"
    skill.path = "/skills/shared/himalaya"

    mock_skill_repo.discover.return_value = [skill]
    mock_skill_repo.get_missing_skill_requirements.return_value = {}

    result = builder.build(user_id="u1")

    assert "Do NOT claim that a file/config exists" in result
    assert "When passing commands to shell()" in result
    # The prompt should explicitly warn against JSON-style escaping with backslashes.
    assert 'export VAR=\\"/abs/path\\"' in result
