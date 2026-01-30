"""Unit tests for memory service.

Tests memory service functionality including:
- Memory search
- Memory context retrieval
- Agent name storage
"""

from unittest.mock import MagicMock, patch

import pytest

from app.config import AgentConfig


class TestMemoryServiceSearch:
    """Tests for memory search functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        config = MagicMock(spec=AgentConfig)
        config.aws_region = "us-east-1"
        config.aws_access_key_id = None
        config.aws_secret_access_key = None
        config.memory_id = None
        config.memory_name = "TestMemory"
        config.memory_description = "Test memory"
        config.memory_retrieval_top_k = 10
        config.memory_retrieval_relevance_score = 0.5
        return config

    @patch("app.services.memory_service.MemoryClient")
    def test_search_memory_returns_facts(self, mock_client_class, mock_config):
        """Test search_memory returns facts from memory."""
        from app.services.memory_service import MemoryService

        # Setup mock
        mock_client = MagicMock()
        mock_client.retrieve_memories.return_value = [
            {"content": {"text": "User likes Python"}}
        ]
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.search_memory(
            user_id="test-user",
            query="programming",
            memory_type="facts"
        )

        assert "User likes Python" in result["facts"]
        mock_client.retrieve_memories.assert_called_once()

    @patch("app.services.memory_service.MemoryClient")
    def test_search_memory_returns_preferences(
        self, mock_client_class, mock_config
    ):
        """Test search_memory returns preferences from memory."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.retrieve_memories.return_value = [
            {"content": {"text": "Prefers concise responses"}}
        ]
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.search_memory(
            user_id="test-user",
            query="response style",
            memory_type="preferences"
        )

        assert "Prefers concise responses" in result["preferences"]

    @patch("app.services.memory_service.MemoryClient")
    def test_search_memory_all_types(self, mock_client_class, mock_config):
        """Test search_memory searches both facts and preferences."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.retrieve_memories.side_effect = [
            [{"content": {"text": "A fact"}}],
            [{"content": {"text": "A preference"}}],
        ]
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.search_memory(
            user_id="test-user",
            query="test",
            memory_type="all"
        )

        assert "A fact" in result["facts"]
        assert "A preference" in result["preferences"]
        assert mock_client.retrieve_memories.call_count == 2

    @patch("app.services.memory_service.MemoryClient")
    def test_search_memory_no_memory_id(self, mock_client_class, mock_config):
        """Test search_memory returns empty when no memory_id."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        service._memory_id = None

        result = service.search_memory(
            user_id="test-user",
            query="test"
        )

        assert result["facts"] == []
        assert result["preferences"] == []

    @patch("app.services.memory_service.MemoryClient")
    def test_search_memory_handles_exception(
        self, mock_client_class, mock_config
    ):
        """Test search_memory handles exceptions gracefully."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.retrieve_memories.side_effect = Exception("API error")
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.search_memory(
            user_id="test-user",
            query="test"
        )

        # Should return empty results, not raise
        assert result["facts"] == []
        assert result["preferences"] == []

    @patch("app.services.memory_service.MemoryClient")
    def test_search_memory_deduplicates_results(
        self, mock_client_class, mock_config
    ):
        """Test search_memory removes duplicate entries."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.retrieve_memories.return_value = [
            {"content": {"text": "Same fact"}},
            {"content": {"text": "Same fact"}},
            {"content": {"text": "Different fact"}},
        ]
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.search_memory(
            user_id="test-user",
            query="test",
            memory_type="facts"
        )

        assert len(result["facts"]) == 2
        assert "Same fact" in result["facts"]
        assert "Different fact" in result["facts"]


class TestMemoryServiceRetrieveContext:
    """Tests for memory context retrieval."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        config = MagicMock(spec=AgentConfig)
        config.aws_region = "us-east-1"
        config.aws_access_key_id = None
        config.aws_secret_access_key = None
        config.memory_id = None
        config.memory_name = "TestMemory"
        config.memory_description = "Test memory"
        config.memory_retrieval_top_k = 10
        config.memory_retrieval_relevance_score = 0.5
        return config

    @patch("app.services.memory_service.MemoryClient")
    def test_retrieve_memory_context_returns_facts_and_prefs(
        self, mock_client_class, mock_config
    ):
        """Test retrieve_memory_context returns both facts and preferences."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.retrieve_memories.side_effect = [
            [{"content": {"text": "User fact"}}],
            [{"content": {"text": "User preference"}}],
        ]
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.retrieve_memory_context(
            user_id="test-user",
            query="hello"
        )

        assert "User fact" in result["facts"]
        assert "User preference" in result["preferences"]


class TestMemoryServiceStoreAgentName:
    """Tests for agent name storage functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        config = MagicMock(spec=AgentConfig)
        config.aws_region = "us-east-1"
        config.aws_access_key_id = None
        config.aws_secret_access_key = None
        config.memory_id = None
        config.memory_name = "TestMemory"
        config.memory_description = "Test memory"
        config.memory_retrieval_top_k = 10
        config.memory_retrieval_relevance_score = 0.5
        return config

    @patch("app.services.memory_service.MemoryClient")
    def test_store_agent_name_success(self, mock_client_class, mock_config):
        """Test store_agent_name stores name successfully."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.store_agent_name(
            user_id="test-user",
            name="Mordecai",
            session_id="test-session"
        )

        assert result is True
        mock_client.create_event.assert_called_once()
        call_kwargs = mock_client.create_event.call_args[1]
        assert call_kwargs["memory_id"] == "test-memory-id"
        assert call_kwargs["actor_id"] == "test-user"
        assert call_kwargs["session_id"] == "test-session"
        assert "Mordecai" in str(call_kwargs["messages"])

    @patch("app.services.memory_service.MemoryClient")
    def test_store_agent_name_creates_memory_if_not_exists(
        self, mock_client_class, mock_config
    ):
        """Test store_agent_name creates memory if memory_id is None."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.list_memories.return_value = []
        mock_client.create_memory_and_wait.return_value = {
            "id": "new-memory-id"
        }
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        # memory_id is None initially

        result = service.store_agent_name(
            user_id="test-user",
            name="Jarvis",
            session_id="test-session"
        )

        assert result is True
        # Should have created memory first
        mock_client.create_memory_and_wait.assert_called_once()
        # Then created the event
        mock_client.create_event.assert_called_once()

    @patch("app.services.memory_service.MemoryClient")
    def test_store_agent_name_handles_create_event_failure(
        self, mock_client_class, mock_config
    ):
        """Test store_agent_name returns False on create_event failure."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.create_event.side_effect = Exception("API error")
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.store_agent_name(
            user_id="test-user",
            name="Mordecai",
            session_id="test-session"
        )

        assert result is False

    @patch("app.services.memory_service.MemoryClient")
    def test_store_agent_name_handles_memory_creation_failure(
        self, mock_client_class, mock_config
    ):
        """Test store_agent_name returns False when memory creation fails."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        mock_client.list_memories.return_value = []
        mock_client.create_memory_and_wait.side_effect = Exception(
            "Memory creation failed"
        )
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        # memory_id is None, will try to create

        result = service.store_agent_name(
            user_id="test-user",
            name="Mordecai",
            session_id="test-session"
        )

        assert result is False


class TestMemoryServiceNameExtraction:
    """Tests for agent name extraction from memory text."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        config = MagicMock(spec=AgentConfig)
        config.aws_region = "us-east-1"
        config.aws_access_key_id = None
        config.aws_secret_access_key = None
        config.memory_id = None
        config.memory_name = "TestMemory"
        config.memory_description = "Test memory"
        config.memory_retrieval_top_k = 10
        config.memory_retrieval_relevance_score = 0.5
        return config

    @patch("app.services.memory_service.MemoryClient")
    def test_extract_name_from_calls_their_assistant_pattern(
        self, mock_client_class, mock_config
    ):
        """Test extraction from 'calls their assistant X' pattern (fact format)."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        
        # This is the actual format stored by AgentCore Memory
        text = "The user calls their assistant Mordecai."
        name = service._extract_name_from_text(text)
        
        assert name == "Mordecai"

    @patch("app.services.memory_service.MemoryClient")
    def test_extract_name_from_my_name_is_pattern(
        self, mock_client_class, mock_config
    ):
        """Test extraction from 'my name is X' pattern."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        
        # Test the pattern used when storing name
        text = "I understand! My name is Mordecai. I'll remember that."
        name = service._extract_name_from_text(text)
        
        assert name == "Mordecai"

    @patch("app.services.memory_service.MemoryClient")
    def test_extract_name_from_call_you_pattern(
        self, mock_client_class, mock_config
    ):
        """Test extraction from 'call you X' pattern."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        
        text = "I want to call you Jarvis. That is your name."
        name = service._extract_name_from_text(text)
        
        assert name == "Jarvis"

    @patch("app.services.memory_service.MemoryClient")
    def test_extract_name_from_call_me_pattern(
        self, mock_client_class, mock_config
    ):
        """Test extraction from 'call me X' pattern."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        
        text = "You can call me Friday from now on."
        name = service._extract_name_from_text(text)
        
        assert name == "Friday"

    @patch("app.services.memory_service.MemoryClient")
    def test_extract_name_skips_common_words(
        self, mock_client_class, mock_config
    ):
        """Test that common words are skipped."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        
        # Test with a realistic pattern where common word appears
        # "my name is" followed by actual name
        text = "My name is Alfred, your helpful assistant"
        name = service._extract_name_from_text(text)
        
        assert name == "Alfred"

    @patch("app.services.memory_service.MemoryClient")
    def test_extract_name_returns_none_for_no_match(
        self, mock_client_class, mock_config
    ):
        """Test that None is returned when no pattern matches."""
        from app.services.memory_service import MemoryService

        service = MemoryService(mock_config)
        
        text = "The weather is nice today."
        name = service._extract_name_from_text(text)
        
        assert name is None

    @patch("app.services.memory_service.MemoryClient")
    def test_retrieve_memory_context_extracts_name_from_preferences(
        self, mock_client_class, mock_config
    ):
        """Test that agent_name is extracted from preferences."""
        from app.services.memory_service import MemoryService

        mock_client = MagicMock()
        # Facts return nothing
        # Preferences return name-related content
        # Identity query in facts returns nothing
        # Identity query in preferences returns the name
        mock_client.retrieve_memories.side_effect = [
            [],  # facts query
            [{"content": {"text": "User prefers concise responses"}}],  # prefs
            [],  # identity facts query
            [{"content": {"text": "My name is Mordecai"}}],  # identity prefs
        ]
        mock_client_class.return_value = mock_client

        service = MemoryService(mock_config)
        service._memory_id = "test-memory-id"

        result = service.retrieve_memory_context(
            user_id="test-user",
            query="what is your name?"
        )

        assert result["agent_name"] == "Mordecai"
