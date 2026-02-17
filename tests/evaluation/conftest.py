"""Pytest fixtures for evaluation tests."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def config():
    """Create a test AgentConfig instance.

    Uses MagicMock to avoid having to provide all required fields
    for the full AgentConfig class.
    """
    mock_config = MagicMock()
    mock_config.skills_base_dir = "/tmp/test_skills"
    mock_config.shared_skills_dir = "/tmp/test_shared_skills"
    mock_config.conversation_window_size = 10
    mock_config.aws_region = "us-west-2"
    mock_config.user_skills_dir_template = None
    mock_config.obsidian_vault_root = None
    mock_config.memory_enabled = False
    mock_config.personality_enabled = True
    mock_config.personality_max_chars = 20000
    mock_config.timezone = "UTC"
    mock_config.agent_commands = None
    mock_config.working_folder_base_dir = "/tmp/test_work"

    return mock_config
