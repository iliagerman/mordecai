"""Unit tests for agent service memory functionality.

Tests the new memory architecture:
- Conversation history tracking
- SlidingWindowConversationManager usage
- Memory context injection
- search_memory tool integration
"""

import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.agent_service import AgentService


class TestAgentServiceConversationHistory:
    """Tests for conversation history tracking."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            conversation_window_size=20,
        )

    def test_add_to_conversation_history(self, config):
        """Test adding messages to conversation history."""
        service = AgentService(config)
        user_id = "test-user"

        service._add_to_conversation_history(user_id, "user", "Hello")
        service._add_to_conversation_history(user_id, "assistant", "Hi there!")

        history = service._get_conversation_history(user_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hi there!"

    def test_get_conversation_history_empty(self, config):
        """Test getting history for user with no messages."""
        service = AgentService(config)

        history = service._get_conversation_history("new-user")
        assert history == []

    def test_clear_session_memory_clears_history(self, config):
        """Test clearing session memory clears conversation history."""
        service = AgentService(config)
        user_id = "test-user"

        service._add_to_conversation_history(user_id, "user", "Hello")
        assert len(service._get_conversation_history(user_id)) == 1

        service._clear_session_memory(user_id)

        assert service._get_conversation_history(user_id) == []

    def test_cleanup_user_clears_history(self, config):
        """Test cleanup_user removes conversation history."""
        service = AgentService(config)
        user_id = "test-user"

        service._add_to_conversation_history(user_id, "user", "Hello")
        service.cleanup_user(user_id)

        assert service._get_conversation_history(user_id) == []

    @patch("app.services.agent_service.Agent")
    @patch("app.services.agent_service.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_message_tracks_history(self, mock_model, mock_agent, config):
        """Test process_message adds messages to history."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        service = AgentService(config)
        user_id = "test-user"

        await service.process_message(user_id, "Hello")

        history = service._get_conversation_history(user_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Response"


class TestAgentServiceConversationManager:
    """Tests for SlidingWindowConversationManager usage."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            conversation_window_size=15,
        )

    @patch("app.services.agent_service.SlidingWindowConversationManager")
    @patch("app.services.agent_service.Agent")
    @patch("app.services.agent_service.BedrockModel")
    def test_create_agent_uses_sliding_window_manager(
        self, mock_model, mock_agent, mock_conv_manager, config
    ):
        """Test _create_agent uses SlidingWindowConversationManager."""
        service = AgentService(config)

        service._create_agent("test-user")

        mock_conv_manager.assert_called_once_with(window_size=config.conversation_window_size)
        mock_agent.assert_called_once()
        call_kwargs = mock_agent.call_args[1]
        assert "conversation_manager" in call_kwargs


class TestAgentServiceMemoryTools:
    """Tests for memory tool integration."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            memory_enabled=True,
        )

    @patch("app.services.agent_service.search_memory_module")
    @patch("app.services.agent_service.Agent")
    @patch("app.services.agent_service.BedrockModel")
    def test_create_agent_includes_search_memory_tool(
        self, mock_model, mock_agent, mock_search_module, config
    ):
        """Test _create_agent includes search_memory tool when memory enabled."""
        mock_memory_service = MagicMock()

        service = AgentService(config, memory_service=mock_memory_service)
        service._create_agent("test-user")

        # Verify search_memory context was set
        mock_search_module.set_memory_context.assert_called_once_with(
            mock_memory_service, "test-user"
        )

    @patch("app.services.agent_service.Agent")
    @patch("app.services.agent_service.BedrockModel")
    def test_create_agent_without_memory_service(self, mock_model, mock_agent, config):
        """Test _create_agent works without memory service."""
        service = AgentService(config, memory_service=None)

        # Should not raise
        service._create_agent("test-user")

        mock_agent.assert_called_once()


class TestAgentServiceSystemPrompt:
    """Tests for system prompt with memory context."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            memory_enabled=True,
        )

    def test_build_system_prompt_includes_memory_capabilities(self, config):
        """Test system prompt includes memory capabilities section."""
        service = AgentService(config)

        prompt = service._build_system_prompt("test-user")

        assert "Memory Capabilities" in prompt
        assert "search_memory" in prompt
        assert "set_agent_name" in prompt

    def test_build_system_prompt_includes_memory_context(self, config):
        """Test system prompt includes retrieved memory context."""
        service = AgentService(config)

        memory_context = {
            "facts": ["User likes Python"],
            "preferences": ["Prefers concise responses"],
        }

        prompt = service._build_system_prompt("test-user", memory_context=memory_context)

        assert "Retrieved Memory" in prompt
        assert "User likes Python" in prompt
        assert "Prefers concise responses" in prompt

    def test_build_system_prompt_without_memory_context(self, config):
        """Test system prompt works without memory context."""
        service = AgentService(config)

        prompt = service._build_system_prompt("test-user")

        assert "Retrieved Memory" not in prompt

    def test_build_system_prompt_memory_disabled(self, temp_dir):
        """Test system prompt without memory capabilities when disabled."""
        config = AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            memory_enabled=False,
        )
        service = AgentService(config)

        prompt = service._build_system_prompt("test-user")

        assert "Memory Capabilities" not in prompt


class TestAgentServiceExplicitRemember:
    """Tests for explicit 'remember ...' handling.

    The agent should persist explicitly requested memories immediately,
    instead of relying on end-of-session extraction.
    """

    @pytest.fixture
    def temp_dir(self):
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            memory_enabled=True,
        )

    @patch("app.services.agent_service.Agent")
    @patch("app.services.agent_service.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_message_stores_explicit_fact(self, _mock_model, mock_agent, config):
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "OK"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        memory_service = MagicMock()
        memory_service.retrieve_memory_context.return_value = {
            "facts": [],
            "preferences": [],
            "agent_name": None,
        }
        memory_service.store_fact.return_value = True

        service = AgentService(config, memory_service=memory_service)

        await service.process_message(
            "test-user",
            "Remember we keep the shopping lists in the family vault.",
        )

        memory_service.store_fact.assert_called()
        _kwargs = memory_service.store_fact.call_args.kwargs
        assert _kwargs["user_id"] == "test-user"
        assert "shopping lists" in _kwargs["fact"].lower()

    @patch("app.services.agent_service.Agent")
    @patch("app.services.agent_service.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_message_does_not_store_sensitive_text(
        self, _mock_model, mock_agent, config
    ):
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "OK"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        memory_service = MagicMock()
        memory_service.retrieve_memory_context.return_value = {
            "facts": [],
            "preferences": [],
            "agent_name": None,
        }

        service = AgentService(config, memory_service=memory_service)

        await service.process_message(
            "test-user",
            "Remember my api_key=sk-THISISNOTREALBUTLOOKSLIKEONE",
        )

        memory_service.store_fact.assert_not_called()
