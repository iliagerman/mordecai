"""Unit tests for agent service.

Tests agent service functionality including:
- Property 1: Model Provider Configuration
- Property 2: Message Processing Produces Response
- Property 12: New Command Clears Short Memory

Requirements: 14.2, 14.3, 1.1, 1.2, 1.3, 1.5, 5.1
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.agent_service import AgentService


class TestAgentServiceModelProvider:
    """Tests for model provider configuration.

    **Property 1: Model Provider Configuration**
    *For any* valid model provider configuration (Bedrock or OpenAI),
    initializing the agent with that configuration should result in
    the agent using the specified provider for inference.
    **Validates: Requirements 1.2**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def bedrock_config(self, temp_dir):
        """Create config for Bedrock provider."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    def openai_config(self, temp_dir):
        """Create config for OpenAI provider."""
        return AgentConfig(
            model_provider=ModelProvider.OPENAI,
            openai_model_id="gpt-4",
            openai_api_key="test-api-key",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @patch("app.services.agent.model_factory.BedrockModel")
    def test_bedrock_model_created_with_config(self, mock_bedrock_model, bedrock_config):
        """Test Bedrock model is created with correct configuration."""
        service = AgentService(bedrock_config)
        service._create_model()

        mock_bedrock_model.assert_called_once_with(
            model_id=bedrock_config.bedrock_model_id,
            region_name=bedrock_config.aws_region,
        )

    @patch("app.services.agent.model_factory.BedrockModel")
    def test_bedrock_api_key_sets_env_var(self, mock_bedrock_model, temp_dir):
        """Test Bedrock API key sets AWS_BEARER_TOKEN_BEDROCK env var."""
        import os

        config = AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            bedrock_api_key="test-bedrock-api-key",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        service = AgentService(config)
        service._create_model()

        assert os.environ.get("AWS_BEARER_TOKEN_BEDROCK") == "test-bedrock-api-key"
        mock_bedrock_model.assert_called_once()

    @patch("app.services.agent.model_factory.OpenAIModel")
    def test_openai_model_created_with_config(self, mock_openai_model, openai_config):
        """Test OpenAI model is created with correct configuration."""
        service = AgentService(openai_config)
        service._create_model()

        mock_openai_model.assert_called_once_with(
            model=openai_config.openai_model_id,
            api_key=openai_config.openai_api_key,
        )

    def test_openai_requires_api_key(self, temp_dir):
        """Test OpenAI provider requires API key."""
        config = AgentConfig(
            model_provider=ModelProvider.OPENAI,
            openai_model_id="gpt-4",
            openai_api_key=None,
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        service = AgentService(config)

        with pytest.raises(ValueError, match="OpenAI API key required"):
            service._create_model()

    def test_get_model_provider_returns_configured_provider(self, bedrock_config):
        """Test get_model_provider returns the configured provider."""
        service = AgentService(bedrock_config)
        assert service.get_model_provider() == ModelProvider.BEDROCK


class TestAgentServiceSession:
    """Tests for session management.

    **Property 12: New Command Clears Short Memory**
    *For any* session with messages in short-term memory, issuing the
    "new" command should result in an empty short-term memory.
    **Validates: Requirements 5.1, 10.1**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
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
        )

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    def test_get_or_create_agent_creates_new_agent(self, mock_model, mock_agent, config):
        """Test get_or_create_agent creates agent for new user."""
        service = AgentService(config)
        user_id = "test-user-1"

        service.get_or_create_agent(user_id)

        mock_agent.assert_called_once()

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    def test_get_or_create_agent_returns_existing_agent(self, mock_model, mock_agent, config):
        """Test get_or_create_agent returns cached agent for the same user."""
        service = AgentService(config)
        user_id = "test-user-1"

        service.get_or_create_agent(user_id)
        service.get_or_create_agent(user_id)

        # Agent is cached per user within a session
        assert mock_agent.call_count == 1

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_new_session_clears_existing_agent(self, mock_model, mock_agent, config):
        """Test new_session creates new agent and clears session manager."""
        service = AgentService(config)
        user_id = "test-user-1"

        # Create initial agent
        service.get_or_create_agent(user_id)
        assert mock_agent.call_count == 1

        # Create new session
        await service.new_session(user_id)

        # Should create a new agent
        assert mock_agent.call_count == 2

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_new_session_clears_session_manager(self, mock_model, mock_agent, config):
        """Test new_session clears conversation history for user."""
        service = AgentService(config)
        user_id = "test-user-1"

        # Add conversation history
        service._add_to_conversation_history(user_id, "user", "Hello")
        assert user_id in service._conversation_history_state._history

        # Create new session
        await service.new_session(user_id)

        # Conversation history should be cleared
        assert user_id not in service._conversation_history

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_new_session_runs_extraction_and_summary_when_available(
        self, mock_model, mock_agent, config
    ):
        """new_session should extract+summarize before clearing state."""
        from unittest.mock import AsyncMock

        mock_memory = MagicMock()

        mock_extraction = MagicMock()
        mock_extraction.extract_and_store = AsyncMock(
            return_value=MagicMock(
                success=True,
                preferences=["Prefers concise"],
                facts=["Keeps shopping lists in the family"],
                commitments=[],
            )
        )
        mock_extraction.summarize_and_store = AsyncMock(
            return_value="- We keep shopping lists in the family vault."
        )

        service = AgentService(
            config,
            memory_service=mock_memory,
            extraction_service=mock_extraction,
        )
        user_id = "test-user-1"

        # Simulate some conversation so /new has something to summarize.
        service._add_to_conversation_history(user_id, "user", "Hello")
        service._add_to_conversation_history(user_id, "assistant", "Hi")
        service.increment_message_count(user_id, 2)

        _agent, notification = await service.new_session(user_id)

        assert mock_extraction.extract_and_store.await_count == 1
        assert mock_extraction.summarize_and_store.await_count == 1
        assert "Summary" in notification

    def test_cleanup_user_removes_session_manager(self, config):
        """Test cleanup_user removes conversation history from memory."""
        service = AgentService(config)
        user_id = "test-user-1"

        # Add conversation history and agent name
        service._add_to_conversation_history(user_id, "user", "Hello")
        service._agent_name_registry.set(user_id, "TestAgent")

        service.cleanup_user(user_id)

        assert user_id not in service._conversation_history_state._history
        assert user_id not in service._agent_name_registry._names


class TestAgentServiceMessageProcessing:
    """Tests for message processing.

    **Property 2: Message Processing Produces Response**
    *For any* valid user message, processing it through the agent
    should produce a non-empty response.
    **Validates: Requirements 1.3**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
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
        )

    @pytest.mark.asyncio
    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    async def test_process_message_returns_response(self, mock_model, mock_agent, config):
        """Test process_message returns agent response."""
        # Setup mock agent response
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Hello! How can I help you?"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        service = AgentService(config)
        response = await service.process_message("user-1", "Hello")

        assert response == "Hello! How can I help you?"

    @pytest.mark.asyncio
    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    async def test_process_message_calls_agent(self, mock_model, mock_agent, config):
        """Test process_message invokes agent with message."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        service = AgentService(config)
        await service.process_message("user-1", "Test message")

        mock_agent_instance.assert_called_once_with("Test message")

    def test_extract_response_text_from_message(self, config):
        """Test _extract_response_text extracts text from message."""
        service = AgentService(config)

        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Extracted text"}]}

        text = service._extract_response_text(mock_result)
        assert text == "Extracted text"

    def test_extract_response_text_fallback(self, config):
        """Test _extract_response_text falls back to str conversion."""
        service = AgentService(config)

        mock_result = MagicMock()
        mock_result.message = None
        mock_result.__str__ = lambda self: "Fallback response"

        text = service._extract_response_text(mock_result)
        assert "Fallback response" in text


class TestAgentServiceMessageCountTracking:
    """Tests for message count tracking.

    **Property 1: Message Count Tracking Accuracy**
    *For any* sequence of user messages and agent responses, the message
    count SHALL equal the total number of messages exchanged, and clearing
    the session SHALL reset the count to zero.
    **Validates: Requirements 1.3, 7.1, 7.3, 7.4**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
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
        )

    def test_get_message_count_returns_zero_for_new_user(self, config):
        """Test get_message_count returns 0 for user with no messages."""
        service = AgentService(config)
        assert service.get_message_count("new-user") == 0

    def test_increment_message_count_increments_by_one(self, config):
        """Test increment_message_count increments by 1 by default."""
        service = AgentService(config)
        user_id = "test-user"

        result = service.increment_message_count(user_id)
        assert result == 1
        assert service.get_message_count(user_id) == 1

    def test_increment_message_count_increments_by_custom_value(self, config):
        """Test increment_message_count increments by custom value."""
        service = AgentService(config)
        user_id = "test-user"

        result = service.increment_message_count(user_id, 5)
        assert result == 5
        assert service.get_message_count(user_id) == 5

    def test_increment_message_count_accumulates(self, config):
        """Test increment_message_count accumulates across calls."""
        service = AgentService(config)
        user_id = "test-user"

        service.increment_message_count(user_id, 3)
        service.increment_message_count(user_id, 2)
        result = service.increment_message_count(user_id, 1)

        assert result == 6
        assert service.get_message_count(user_id) == 6

    def test_reset_message_count_sets_to_zero(self, config):
        """Test reset_message_count sets count to zero."""
        service = AgentService(config)
        user_id = "test-user"

        service.increment_message_count(user_id, 10)
        assert service.get_message_count(user_id) == 10

        service.reset_message_count(user_id)
        assert service.get_message_count(user_id) == 0

    def test_message_counts_are_per_user(self, config):
        """Test message counts are tracked separately per user."""
        service = AgentService(config)

        service.increment_message_count("user-1", 5)
        service.increment_message_count("user-2", 3)

        assert service.get_message_count("user-1") == 5
        assert service.get_message_count("user-2") == 3

    def test_cleanup_user_removes_message_count(self, config):
        """Test cleanup_user removes message count for user."""
        service = AgentService(config)
        user_id = "test-user"

        service.increment_message_count(user_id, 10)
        assert service.get_message_count(user_id) == 10

        service.cleanup_user(user_id)
        assert service.get_message_count(user_id) == 0


class TestAgentServiceAutomaticExtraction:
    """Tests for automatic extraction on conversation limit.

    Tests subtasks 6.1-6.4:
    - 6.1: Add limit check in process_message
    - 6.2: Implement non-blocking extraction trigger
    - 6.3: Implement extraction and session clearing flow
    - 6.4: Notify user of extraction
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config_with_low_limit(self, temp_dir):
        """Create config with low message limit for testing."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            max_conversation_messages=4,  # Low limit for testing
            extraction_timeout_seconds=5,
        )

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_message_increments_count_for_user_and_response(
        self, mock_model, mock_agent, config_with_low_limit
    ):
        """Test that process_message increments count for both messages."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        service = AgentService(config_with_low_limit)
        user_id = "test-user"

        # Initial count should be 0
        assert service.get_message_count(user_id) == 0

        # Process one message
        await service.process_message(user_id, "Hello")

        # Count should be 2 (user message + agent response)
        assert service.get_message_count(user_id) == 2

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_message_triggers_extraction_at_limit(
        self, mock_model, mock_agent, config_with_low_limit
    ):
        """Test that extraction is triggered when limit is reached."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        mock_extraction = MagicMock()
        mock_extraction.extract_and_store = MagicMock(
            return_value=MagicMock(
                success=True,
                preferences=[],
                facts=[],
                commitments=[],
            )
        )

        service = AgentService(
            config_with_low_limit,
            extraction_service=mock_extraction,
        )
        user_id = "test-user"

        # Process messages until limit (4 messages = 2 exchanges)
        await service.process_message(user_id, "Hello")  # count = 2
        response = await service.process_message(user_id, "Hi")  # count = 4

        # Response should contain extraction notification
        assert "summarized" in response.lower()
        assert "saved" in response.lower()

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_message_resets_count_after_extraction(
        self, mock_model, mock_agent, config_with_low_limit
    ):
        """Test that message count is reset after extraction."""
        import asyncio

        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        async def mock_extract(*args, **kwargs):
            return MagicMock(
                success=True,
                preferences=[],
                facts=[],
                commitments=[],
            )

        mock_extraction = MagicMock()
        mock_extraction.extract_and_store = mock_extract

        service = AgentService(
            config_with_low_limit,
            extraction_service=mock_extraction,
        )
        user_id = "test-user"

        # Process messages until limit
        await service.process_message(user_id, "Hello")  # count = 2
        await service.process_message(user_id, "Hi")  # count = 4, triggers

        # Give async task time to complete
        await asyncio.sleep(0.1)

        # Count should be reset to 0
        assert service.get_message_count(user_id) == 0

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_extraction_not_triggered_below_limit(
        self, mock_model, mock_agent, config_with_low_limit
    ):
        """Test that extraction is not triggered below the limit."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        mock_extraction = MagicMock()

        service = AgentService(
            config_with_low_limit,
            extraction_service=mock_extraction,
        )
        user_id = "test-user"

        # Process one message (count = 2, below limit of 4)
        response = await service.process_message(user_id, "Hello")

        # Response should NOT contain extraction notification
        assert "summarized" not in response.lower()

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_extraction_clears_session_memory(
        self, mock_model, mock_agent, config_with_low_limit
    ):
        """Test that conversation history is cleared after extraction."""
        import asyncio

        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        async def mock_extract(*args, **kwargs):
            return MagicMock(
                success=True,
                preferences=[],
                facts=[],
                commitments=[],
            )

        mock_extraction = MagicMock()
        mock_extraction.extract_and_store = mock_extract

        service = AgentService(
            config_with_low_limit,
            extraction_service=mock_extraction,
        )
        user_id = "test-user"

        # Add conversation history
        service._add_to_conversation_history(user_id, "user", "Previous msg")

        # Process messages until limit
        await service.process_message(user_id, "Hello")
        await service.process_message(user_id, "Hi")

        # Give async task time to complete
        await asyncio.sleep(0.1)

        # Conversation history should be cleared
        assert user_id not in service._conversation_history_state._history


class TestVisionModelSelection:
    """Tests for vision model selection logic.

    **Property 7: Vision Model Selection**
    *For any* image attachment, IF vision_model_id is configured, the system
    SHALL use the vision model. IF vision_model_id is not configured, the
    system SHALL use the default model.
    **Validates: Requirements 3.1, 3.2, 3.3**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config_with_vision(self, temp_dir):
        """Create config with vision model configured."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            vision_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    def config_without_vision(self, temp_dir):
        """Create config without vision model configured."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            vision_model_id=None,
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @patch("app.services.agent.model_factory.BedrockModel")
    def test_vision_model_used_when_configured_and_requested(
        self, mock_bedrock_model, config_with_vision
    ):
        """Test vision model is used when configured and use_vision=True."""
        service = AgentService(config_with_vision)
        service._create_model(use_vision=True)

        # Should be called with vision_model_id
        mock_bedrock_model.assert_called_once_with(
            model_id=config_with_vision.vision_model_id,
            region_name=config_with_vision.aws_region,
        )

    @patch("app.services.agent.model_factory.BedrockModel")
    def test_default_model_used_when_vision_not_configured(
        self, mock_bedrock_model, config_without_vision
    ):
        """Test default model is used when vision_model_id is not set."""
        service = AgentService(config_without_vision)
        service._create_model(use_vision=True)

        # Should fall back to default bedrock_model_id
        mock_bedrock_model.assert_called_once_with(
            model_id=config_without_vision.bedrock_model_id,
            region_name=config_without_vision.aws_region,
        )

    @patch("app.services.agent.model_factory.BedrockModel")
    def test_default_model_used_when_use_vision_false(self, mock_bedrock_model, config_with_vision):
        """Test default model is used when use_vision=False."""
        service = AgentService(config_with_vision)
        service._create_model(use_vision=False)

        # Should use default bedrock_model_id, not vision_model_id
        mock_bedrock_model.assert_called_once_with(
            model_id=config_with_vision.bedrock_model_id,
            region_name=config_with_vision.aws_region,
        )


class TestImageCaptionInclusion:
    """Tests for image caption inclusion in vision processing.

    **Property 8: Image Caption Inclusion**
    *For any* image attachment with an accompanying text caption, both the
    image and caption SHALL be included in the agent's message context.
    **Validates: Requirements 3.6**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration with vision model."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            vision_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    def temp_image(self, temp_dir):
        """Create a temporary test image file."""
        image_path = Path(temp_dir) / "test_image.png"
        # Create a minimal PNG file (1x1 pixel)
        import base64

        # Minimal valid PNG (1x1 transparent pixel)
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
        )
        image_path.write_bytes(png_data)
        return str(image_path)

    def test_prepare_image_content_includes_caption(self, config, temp_image):
        """Test _prepare_image_content includes caption when provided."""
        service = AgentService(config)
        caption = "What is in this image?"

        content = service._prepare_image_content(temp_image, caption=caption)

        # Should have 2 blocks: image and text
        assert len(content) == 2
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "text"
        assert content[1]["text"] == caption

    def test_prepare_image_content_without_caption(self, config, temp_image):
        """Test _prepare_image_content works without caption."""
        service = AgentService(config)

        content = service._prepare_image_content(temp_image, caption=None)

        # Should have only 1 block: image
        assert len(content) == 1
        assert content[0]["type"] == "image"

    def test_prepare_image_content_base64_encoding(self, config, temp_image):
        """Test _prepare_image_content properly base64 encodes image."""
        import base64

        service = AgentService(config)

        content = service._prepare_image_content(temp_image)

        # Verify base64 data is valid
        image_block = content[0]
        assert image_block["source"]["type"] == "base64"
        # Should be able to decode without error
        decoded = base64.b64decode(image_block["source"]["data"])
        assert len(decoded) > 0

    def test_get_media_type_from_extension(self, config):
        """Test media type detection from file extension."""
        service = AgentService(config)

        assert service._get_media_type_from_extension("test.png") == "image/png"
        assert service._get_media_type_from_extension("test.jpg") == "image/jpeg"
        assert service._get_media_type_from_extension("test.jpeg") == "image/jpeg"
        assert service._get_media_type_from_extension("test.gif") == "image/gif"
        assert service._get_media_type_from_extension("test.webp") == "image/webp"
        # Unknown extension defaults to png
        assert service._get_media_type_from_extension("test.bmp") == "image/png"


class TestVisionProcessingFallback:
    """Tests for vision processing fallback behavior.

    Tests subtask 7.5: Implement vision processing fallback
    - Catch model errors for unsupported images
    - Fall back to treating image as file attachment
    - Return helpful message explaining limitation
    **Validates: Requirements 3.4, 8.4**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            vision_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    def temp_image(self, temp_dir):
        """Create a temporary test image file."""
        import base64

        image_path = Path(temp_dir) / "test_image.png"
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
        )
        image_path.write_bytes(png_data)
        return str(image_path)

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_fallback_on_agent_error(self, mock_model, mock_agent, config, temp_image):
        """Test fallback message when agent raises an error."""
        # Make agent raise an exception
        mock_agent.side_effect = Exception("Model does not support images")

        service = AgentService(config)
        response = await service.process_image_message(
            user_id="test-user",
            message="What is this?",
            image_path=temp_image,
        )

        # Should return fallback message with file path
        assert "couldn't process it visually" in response
        assert temp_image in response
        assert "file system tools" in response

    @patch("strands.agent.conversation_manager.SlidingWindowConversationManager")
    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_successful_vision_processing(
        self, mock_model, mock_agent, mock_conv_manager, config, temp_image
    ):
        """Test successful vision processing returns agent response."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "I see a test image."}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        service = AgentService(config)
        response = await service.process_image_message(
            user_id="test-user",
            message="What is this?",
            image_path=temp_image,
        )

        assert response == "I see a test image."

    @patch("strands.agent.conversation_manager.SlidingWindowConversationManager")
    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_process_image_increments_message_count(
        self, mock_model, mock_agent, mock_conv_manager, config, temp_image
    ):
        """Test process_image_message increments message count."""
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Response"}]}
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = mock_result
        mock_agent.return_value = mock_agent_instance

        service = AgentService(config)
        user_id = "test-user"

        assert service.get_message_count(user_id) == 0

        await service.process_image_message(
            user_id=user_id,
            message="Test",
            image_path=temp_image,
        )

        # Should increment by 2 (user message + agent response)
        assert service.get_message_count(user_id) == 2


class TestAgentServiceWorkingFolder:
    """Tests for working folder in system prompt.

    **Property 15: Working Folder Path in System Prompt**
    *For any* agent invocation, the system prompt SHALL contain
    the user's working folder path.
    **Validates: Requirements 10.3**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
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
            working_folder_base_dir=temp_dir,
        )

    def test_system_prompt_contains_working_folder(self, config):
        """Test that system prompt contains working folder path."""
        service = AgentService(config)
        user_id = "test-user"

        prompt = service._build_system_prompt(user_id)

        assert "Working Folder" in prompt
        assert user_id in prompt
        assert "read and write access" in prompt.lower()

    def test_system_prompt_contains_working_folder_section(self, config):
        """Test that system prompt has working folder section."""
        service = AgentService(config)
        user_id = "test-user-123"

        prompt = service._build_system_prompt(user_id)

        # Should have the section header
        assert "## Working Folder" in prompt
        # Should mention the folder path
        assert "Your working folder is:" in prompt

    def test_working_folder_path_is_user_specific(self, config):
        """Test that working folder path contains user ID."""
        service = AgentService(config)
        user_id = "unique-user-456"

        prompt = service._build_system_prompt(user_id)

        # The path should contain the user ID
        assert user_id in prompt


class TestAgentServiceNewSessionClearsWorkingFolder:
    """Tests for clearing working folder on new session.

    **Property 16: Working Folder Cleared on New Session**
    *For any* user starting a new session, the working folder
    SHALL be empty after session creation completes.
    **Validates: Requirements 10.4**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
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
            working_folder_base_dir=temp_dir,
        )

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_new_session_clears_working_folder(self, mock_model, mock_agent, config):
        """Test that new_session clears the working folder."""
        from app.services.file_service import FileService

        file_service = FileService(config)
        service = AgentService(config, file_service=file_service)
        user_id = "test-user"

        # Create a file in the working folder
        working_dir = file_service.get_user_working_dir(user_id)
        test_file = working_dir / "test_file.txt"
        test_file.write_text("test content")
        assert test_file.exists()

        # Create new session
        await service.new_session(user_id)

        # Working folder should be cleared
        assert not test_file.exists()


class TestAgentServicePersonalityInjection:
    """Tests for Obsidian personality injection in system prompt.

    Personality files are loaded from an external Obsidian vault root.

    Layout:
      - me/default/{soul,id}.md
      - me/<TELEGRAM_ID>/{soul,id}.md

    Resolution order: per-user file if present, else default.
    """

    @pytest.fixture
    def vault_dir(self, tmp_path):
        # Use pytest's tmp_path so we don't depend on other class-scoped fixtures.
        return str(tmp_path / "vault")

    @pytest.fixture
    def config(self, tmp_path, vault_dir):
        base = str(tmp_path / "agent")
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=base,
            skills_base_dir=base,
            working_folder_base_dir=base,
            obsidian_vault_root=vault_dir,
            personality_enabled=True,
            personality_max_chars=20_000,
        )

    def _write(self, vault_dir: str, relpath: str, content: str) -> None:
        path = Path(vault_dir) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_falls_back_to_default_when_user_missing(self, config, vault_dir):
        service = AgentService(config)
        user_id = "12345"

        self._write(vault_dir, "me/default/soul.md", "DEFAULT_SOUL")
        self._write(vault_dir, "me/default/id.md", "DEFAULT_ID")

        prompt = service._build_system_prompt(user_id)

        assert "## Personality (Obsidian Vault)" in prompt
        assert "## Obsidian Vault Access" in prompt
        assert "bounded search" in prompt.lower()
        assert "DEFAULT_SOUL" in prompt
        assert "DEFAULT_ID" in prompt
        assert "source: default" in prompt

    def test_user_files_override_default(self, config, vault_dir):
        service = AgentService(config)
        user_id = "999"

        self._write(vault_dir, "me/default/soul.md", "DEFAULT_SOUL")
        self._write(vault_dir, "me/default/id.md", "DEFAULT_ID")
        self._write(vault_dir, f"me/{user_id}/soul.md", "USER_SOUL")
        self._write(vault_dir, f"me/{user_id}/id.md", "USER_ID")

        prompt = service._build_system_prompt(user_id)

        assert "USER_SOUL" in prompt
        assert "USER_ID" in prompt
        assert "DEFAULT_SOUL" not in prompt
        assert "DEFAULT_ID" not in prompt
        assert "source: user" in prompt

    @patch("strands.Agent")
    @patch("app.services.agent.model_factory.BedrockModel")
    @pytest.mark.asyncio
    async def test_new_session_without_file_service_succeeds(self, mock_model, mock_agent, config):
        """Test that new_session works without file service."""
        service = AgentService(config)  # No file_service
        user_id = "test-user"

        # Should not raise an error
        agent, notification = await service.new_session(user_id)

        assert agent is not None
        assert "New session started" in notification


class TestGeminiModelProvider:
    """Tests for Google Gemini model provider configuration.

    **Property 1: Model Provider Configuration (Extended for Gemini)**
    *For any* valid Google Gemini configuration, initializing the agent
    with that configuration should result in the agent using Gemini.
    **Validates: Requirements 1.1, 2.1, 2.2, 4.1, 4.2, 4.3, 4.4, 7.1, 7.2, 7.3**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def google_config(self, temp_dir):
        """Create config for Google Gemini provider."""
        return AgentConfig(
            model_provider=ModelProvider.GOOGLE,
            google_model_id="gemini-2.5-flash",
            google_api_key="test-google-api-key",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    def test_google_enum_value_exists(self):
        """Test ModelProvider.GOOGLE enum value exists and equals 'google'."""
        assert ModelProvider.GOOGLE == "google"
        assert ModelProvider.GOOGLE.value == "google"

    def test_google_config_defaults(self, temp_dir):
        """Test AgentConfig google fields have correct defaults."""
        config = AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        assert config.google_model_id == "gemini-2.5-flash"
        assert config.google_api_key is None

    def test_google_config_accepts_custom_values(self, temp_dir):
        """Test AgentConfig accepts custom google values."""
        config = AgentConfig(
            model_provider=ModelProvider.GOOGLE,
            google_model_id="gemini-2.0-pro",
            google_api_key="custom-api-key",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        assert config.google_model_id == "gemini-2.0-pro"
        assert config.google_api_key == "custom-api-key"

    @patch("strands.models.gemini.GeminiModel")
    def test_gemini_model_created_with_config(self, mock_gemini_model, google_config):
        """Test GeminiModel is created with correct configuration."""
        service = AgentService(google_config)
        service._create_model()

        mock_gemini_model.assert_called_once_with(
            client_args={"api_key": google_config.google_api_key},
            model_id=google_config.google_model_id,
            params={"max_output_tokens": 4096},
        )

    @patch("strands.models.gemini.GeminiModel")
    def test_gemini_model_receives_api_key_in_client_args(self, mock_gemini_model, temp_dir):
        """Test GeminiModel receives api_key in client_args."""
        config = AgentConfig(
            model_provider=ModelProvider.GOOGLE,
            google_model_id="gemini-2.5-flash",
            google_api_key="my-secret-key",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        service = AgentService(config)
        service._create_model()

        call_kwargs = mock_gemini_model.call_args[1]
        assert call_kwargs["client_args"]["api_key"] == "my-secret-key"

    @patch("strands.models.gemini.GeminiModel")
    def test_gemini_model_receives_model_id(self, mock_gemini_model, temp_dir):
        """Test GeminiModel receives correct model_id."""
        config = AgentConfig(
            model_provider=ModelProvider.GOOGLE,
            google_model_id="gemini-2.0-pro",
            google_api_key="test-key",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        service = AgentService(config)
        service._create_model()

        call_kwargs = mock_gemini_model.call_args[1]
        assert call_kwargs["model_id"] == "gemini-2.0-pro"

    def test_google_requires_api_key(self, temp_dir):
        """Test Google provider requires API key."""
        config = AgentConfig(
            model_provider=ModelProvider.GOOGLE,
            google_model_id="gemini-2.5-flash",
            google_api_key=None,
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )
        service = AgentService(config)

        with pytest.raises(ValueError, match="Google API key required"):
            service._create_model()

    def test_get_model_provider_returns_google(self, google_config):
        """Test get_model_provider returns GOOGLE when configured."""
        service = AgentService(google_config)
        assert service.get_model_provider() == ModelProvider.GOOGLE
