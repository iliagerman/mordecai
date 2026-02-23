"""Integration tests for AgentCore memory system.

Tests verify:
- AgentCoreMemorySessionManager is used when memory_enabled=True
- No session manager when memory_enabled=False
- Actor ID is correctly derived from user ID
- Session IDs are unique

Requirements: 1.2, 4.1
"""

import pytest
import shutil
import tempfile
import time
from unittest.mock import MagicMock, patch

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.memory_service import MemoryService


@pytest.fixture
def temp_dirs():
    """Create temporary directories for test isolation."""
    session_dir = tempfile.mkdtemp(prefix="test_sessions_")
    skills_dir = tempfile.mkdtemp(prefix="test_skills_")
    workspace_dir = tempfile.mkdtemp(prefix="test_workspace_")
    yield session_dir, skills_dir, workspace_dir
    # Cleanup
    shutil.rmtree(session_dir, ignore_errors=True)
    shutil.rmtree(skills_dir, ignore_errors=True)
    shutil.rmtree(workspace_dir, ignore_errors=True)


@pytest.fixture
def base_config():
    """Load base config from files (includes secrets.yml)."""
    return AgentConfig.from_json_file(
        config_path="config.json",
        secrets_path="secrets.yml",
    )


@pytest.fixture
def memory_config(temp_dirs, base_config):
    """Create config with memory enabled."""
    session_dir, skills_dir, workspace_dir = temp_dirs

    return AgentConfig(
        telegram_bot_token=base_config.telegram_bot_token,
        model_provider=base_config.model_provider,
        bedrock_model_id=base_config.bedrock_model_id,
        bedrock_api_key=base_config.bedrock_api_key,
        aws_region=base_config.aws_region,
        memory_enabled=True,
        memory_id="test-memory-id",  # Use a test memory ID
        memory_name="TestMemory",
        session_storage_dir=session_dir,
        skills_base_dir=skills_dir,
        working_folder_base_dir=workspace_dir,
    )


@pytest.fixture
def disabled_memory_config(temp_dirs, base_config):
    """Create config with memory disabled."""
    session_dir, skills_dir, workspace_dir = temp_dirs

    return AgentConfig(
        telegram_bot_token=base_config.telegram_bot_token,
        model_provider=base_config.model_provider,
        bedrock_model_id=base_config.bedrock_model_id,
        bedrock_api_key=base_config.bedrock_api_key,
        aws_region=base_config.aws_region,
        memory_enabled=False,
        session_storage_dir=session_dir,
        skills_base_dir=skills_dir,
        working_folder_base_dir=workspace_dir,
    )


@pytest.fixture
def mock_memory_service(memory_config):
    """Create a mock memory service that returns mock session managers."""
    service = MagicMock(spec=MemoryService)

    def create_mock_session_manager(user_id: str, session_id: str):
        """Create a mock session manager with config attributes."""
        mock_sm = MagicMock()
        mock_config = MagicMock()
        mock_config.actor_id = user_id
        mock_config.session_id = session_id
        mock_sm._config = mock_config
        mock_sm.agentcore_memory_config = mock_config
        return mock_sm

    service.create_session_manager.side_effect = create_mock_session_manager
    return service


class TestAgentCoreMemoryPersistence:
    """Tests for AgentCore memory persistence."""

    def test_memory_enabled_calls_memory_service(self, memory_config, mock_memory_service):
        """Verify memory service is called when memory_enabled=True.

        Requirements: 1.2
        """
        service = AgentService(memory_config, mock_memory_service)
        agent = service.get_or_create_agent("test_user_1")

        # Verify memory service was called to create session manager
        mock_memory_service.create_session_manager.assert_called_once()
        call_args = mock_memory_service.create_session_manager.call_args
        assert call_args.kwargs["user_id"] == "test_user_1"

        # Verify agent was created
        assert agent is not None

    def test_memory_disabled_no_memory_service_call(
        self, disabled_memory_config, mock_memory_service
    ):
        """Verify memory service is NOT called when memory_enabled=False.

        Requirements: 4.1
        """
        # Memory disabled, but service provided - should not be called
        service = AgentService(disabled_memory_config, mock_memory_service)
        agent = service.get_or_create_agent("test_user_2")

        # Memory service should NOT be called
        mock_memory_service.create_session_manager.assert_not_called()

        # Agent should still be created
        assert agent is not None

    def test_memory_enabled_without_service(self, memory_config):
        """Verify agent works when memory_enabled but no service provided.

        This tests graceful degradation when MemoryService is not available.
        """
        # Memory enabled but no service provided
        service = AgentService(memory_config, memory_service=None)
        agent = service.get_or_create_agent("test_user_3")

        # Agent should still be created (graceful degradation)
        assert agent is not None

    def test_actor_id_matches_user_id(self, memory_config, mock_memory_service):
        """Verify actor_id is derived from user_id.

        Requirements: 7.1
        """
        user_id = "test_user_123"
        service = AgentService(memory_config, mock_memory_service)
        service.get_or_create_agent(user_id)

        # Verify the session manager was created with correct actor_id
        call_args = mock_memory_service.create_session_manager.call_args
        assert call_args.kwargs["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_new_session_creates_new_session_id(self, memory_config, mock_memory_service):
        """Verify new_session creates a fresh session with new session_id.

        Requirements: 2.3, 2.4
        """
        user_id = "test_user_session"
        service = AgentService(memory_config, mock_memory_service)

        # Create first agent
        service.get_or_create_agent(user_id)
        first_call = mock_memory_service.create_session_manager.call_args
        first_session_id = first_call.kwargs["session_id"]

        # Wait a bit to ensure timestamp changes
        time.sleep(1.1)

        # Create new session
        await service.new_session(user_id)
        second_call = mock_memory_service.create_session_manager.call_args
        second_session_id = second_call.kwargs["session_id"]

        # Session IDs should be different
        assert first_session_id != second_session_id

    def test_session_id_format(self, memory_config, mock_memory_service):
        """Verify session_id follows expected format."""
        user_id = "test_user_format"
        service = AgentService(memory_config, mock_memory_service)

        service.get_or_create_agent(user_id)

        call_args = mock_memory_service.create_session_manager.call_args
        session_id = call_args.kwargs["session_id"]

        # Session ID should start with "session_" and contain user_id
        assert session_id.startswith("session_")
        assert user_id in session_id

    def test_memory_service_failure_graceful_degradation(self, memory_config):
        """Verify agent works when memory service fails.

        Requirements: 5.3 (graceful degradation)
        """
        # Create a memory service that raises an exception
        failing_service = MagicMock(spec=MemoryService)
        failing_service.create_session_manager.side_effect = Exception("Memory service unavailable")

        service = AgentService(memory_config, failing_service)

        # Agent should still be created without memory
        agent = service.get_or_create_agent("test_user_fail")
        assert agent is not None

    def test_no_mem0_memory_tool(self, memory_config, mock_memory_service):
        """Verify mem0_memory tool is NOT in agent tools.

        Requirements: 5.2, 5.5
        """
        service = AgentService(memory_config, mock_memory_service)
        agent = service.get_or_create_agent("test_user_tools")

        # Check that mem0_memory tool is NOT available
        tool_names = [t.tool_name for t in agent.tool_registry.registry.values()]
        assert "mem0_memory" not in tool_names

    def test_forget_memory_tool_available(self, memory_config, mock_memory_service):
        """Verify forget_memory tool IS available when memory is enabled.

        This ensures runtime supports deleting incorrect/outdated memories.
        """
        service = AgentService(memory_config, mock_memory_service)
        agent = service.get_or_create_agent("test_user_forget_tool")

        tool_names = [t.tool_name for t in agent.tool_registry.registry.values()]
        assert "forget_memory" in tool_names

    def test_system_prompt_includes_memory_section(self, memory_config, mock_memory_service):
        """Verify system prompt includes memory capabilities when enabled."""
        service = AgentService(memory_config, mock_memory_service)

        prompt = service._build_system_prompt("test_user_prompt")

        assert "Memory Capabilities" in prompt
        assert "persistent memory" in prompt.lower()

    def test_system_prompt_no_memory_section_when_disabled(self, disabled_memory_config):
        """Verify system prompt excludes memory section when disabled."""
        service = AgentService(disabled_memory_config, memory_service=None)

        prompt = service._build_system_prompt("test_user_prompt")

        assert "Memory Capabilities" not in prompt

    def test_get_session_id_returns_current_session(self, memory_config, mock_memory_service):
        """Verify get_session_id returns the current session ID."""
        user_id = "test_user_get_session"
        service = AgentService(memory_config, mock_memory_service)

        # Before creating agent, no session
        assert service.get_session_id(user_id) is None

        # After creating agent, session exists
        service.get_or_create_agent(user_id)
        session_id = service.get_session_id(user_id)

        assert session_id is not None
        assert user_id in session_id

    def test_multiple_users_have_different_sessions(self, memory_config, mock_memory_service):
        """Verify different users get different session IDs."""
        service = AgentService(memory_config, mock_memory_service)

        service.get_or_create_agent("user_a")
        service.get_or_create_agent("user_b")

        session_a = service.get_session_id("user_a")
        session_b = service.get_session_id("user_b")

        assert session_a != session_b
        assert "user_a" in session_a
        assert "user_b" in session_b
