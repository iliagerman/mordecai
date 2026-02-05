"""Unit tests for search_memory tool.

Tests the search_memory tool functionality including:
- Successful memory search
- Empty results handling
- Error handling
- Memory type filtering
"""

from unittest.mock import MagicMock

import pytest

from app.tools.search_memory import (
    search_memory,
    set_memory_context,
)


class TestSearchMemoryFunction:
    """Tests for search_memory function."""

    @pytest.fixture(autouse=True)
    def reset_globals(self):
        """Reset global state before each test."""
        import app.tools.search_memory as module
        module._memory_service = None
        module._current_user_id = None
        yield
        module._memory_service = None
        module._current_user_id = None

    @pytest.fixture
    def mock_memory_service(self):
        """Create a mock memory service."""
        return MagicMock()

    def test_search_memory_no_query(self):
        """Test search_memory with no query returns error."""
        result = search_memory(query="")
        assert "No search query" in result

    def test_search_memory_no_memory_service(self):
        """Test search_memory without memory service returns error."""
        result = search_memory(query="test query")
        assert "Memory service not available" in result

    def test_search_memory_no_user_id(self, mock_memory_service):
        """Test search_memory without user ID returns error."""
        set_memory_context(mock_memory_service, None)

        result = search_memory(query="test query")
        assert "User context not available" in result

    def test_search_memory_success_with_facts(self, mock_memory_service):
        """Test search_memory returns facts successfully."""
        mock_memory_service.search_memory.return_value = {
            "facts": ["User likes Python", "User works at Acme"],
            "preferences": []
        }
        set_memory_context(mock_memory_service, "test-user")

        result = search_memory(query="test query")

        assert "Facts I remember" in result
        assert "User likes Python" in result

    def test_search_memory_success_with_preferences(self, mock_memory_service):
        """Test search_memory returns preferences successfully."""
        mock_memory_service.search_memory.return_value = {
            "facts": [],
            "preferences": ["Prefers concise responses"]
        }
        set_memory_context(mock_memory_service, "test-user")

        result = search_memory(query="test query")

        assert "Your preferences" in result
        assert "Prefers concise responses" in result

    def test_search_memory_no_results(self, mock_memory_service):
        """Test search_memory with no results."""
        mock_memory_service.search_memory.return_value = {
            "facts": [],
            "preferences": []
        }
        set_memory_context(mock_memory_service, "test-user")

        result = search_memory(query="test query")

        assert "No memories found" in result

    def test_search_memory_with_memory_type(self, mock_memory_service):
        """Test search_memory respects memory_type parameter."""
        mock_memory_service.search_memory.return_value = {
            "facts": ["A fact"],
            "preferences": []
        }
        set_memory_context(mock_memory_service, "test-user")

        search_memory(query="test", memory_type="facts")

        mock_memory_service.search_memory.assert_called_once_with(
            user_id="test-user",
            query="test",
            memory_type="facts"
        )

    def test_search_memory_exception_handling(self, mock_memory_service):
        """Test search_memory handles exceptions gracefully."""
        mock_memory_service.search_memory.side_effect = Exception("DB error")
        set_memory_context(mock_memory_service, "test-user")

        result = search_memory(query="test query")

        assert "Error searching memory" in result
