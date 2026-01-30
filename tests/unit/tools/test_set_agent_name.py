"""Tests for set_agent_name tool."""

from unittest.mock import MagicMock

import pytest

from app.tools.set_agent_name import (
    set_agent_name,
    set_memory_service,
    _memory_service,
    _current_user_id,
    _current_session_id,
)


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    import app.tools.set_agent_name as module
    module._memory_service = None
    module._current_user_id = None
    module._current_session_id = None
    yield
    module._memory_service = None
    module._current_user_id = None
    module._current_session_id = None


class TestSetAgentName:
    """Tests for set_agent_name tool function."""

    def test_returns_error_when_no_name_provided(self):
        """Should return error when name is empty."""
        tool = {"toolUseId": "test-123", "input": {"name": ""}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        assert result["toolUseId"] == "test-123"
        assert "No name provided" in result["content"][0]["text"]

    def test_returns_error_when_name_is_whitespace(self):
        """Should return error when name is only whitespace."""
        tool = {"toolUseId": "test-123", "input": {"name": "   "}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        assert "No name provided" in result["content"][0]["text"]

    def test_returns_error_when_memory_service_not_set(self):
        """Should return error when memory service is not available."""
        tool = {"toolUseId": "test-123", "input": {"name": "Mordecai"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        assert "Memory service not available" in result["content"][0]["text"]

    def test_returns_error_when_user_id_not_set(self):
        """Should return error when user context is not available."""
        import app.tools.set_agent_name as module
        module._memory_service = MagicMock()
        
        tool = {"toolUseId": "test-123", "input": {"name": "Mordecai"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        assert "User context not available" in result["content"][0]["text"]

    def test_returns_error_when_session_id_not_set(self):
        """Should return error when session context is not available."""
        import app.tools.set_agent_name as module
        module._memory_service = MagicMock()
        module._current_user_id = "test-user"
        
        tool = {"toolUseId": "test-123", "input": {"name": "Mordecai"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        assert "Session context not available" in result["content"][0]["text"]

    def test_returns_success_when_name_stored(self):
        """Should return success when name is stored successfully."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        mock_memory.store_agent_name.return_value = True
        module._memory_service = mock_memory
        module._current_user_id = "test-user"
        module._current_session_id = "test-session"
        
        tool = {"toolUseId": "test-123", "input": {"name": "Mordecai"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "success"
        assert "stored my name as 'Mordecai'" in result["content"][0]["text"]
        assert "remember this name across" in result["content"][0]["text"]
        mock_memory.store_agent_name.assert_called_once_with(
            "test-user", "Mordecai", "test-session"
        )

    def test_returns_error_with_session_only_message_when_storage_fails(self):
        """Should return error with clear message when storage fails."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        mock_memory.store_agent_name.return_value = False
        module._memory_service = mock_memory
        module._current_user_id = "test-user"
        module._current_session_id = "test-session"
        
        tool = {"toolUseId": "test-123", "input": {"name": "Mordecai"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        text = result["content"][0]["text"]
        assert "Failed to store name 'Mordecai'" in text
        assert "Memory storage is not available" in text
        assert "this session only" in text
        assert "won't remember it in future sessions" in text


class TestSetMemoryService:
    """Tests for set_memory_service function."""

    def test_sets_memory_service_and_context(self):
        """Should set memory service, user_id, and session_id."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        
        set_memory_service(mock_memory, "user-123", "session-456")
        
        assert module._memory_service is mock_memory
        assert module._current_user_id == "user-123"
        assert module._current_session_id == "session-456"

    def test_sets_memory_service_without_session_id(self):
        """Should set memory service with None session_id."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        
        set_memory_service(mock_memory, "user-123")
        
        assert module._memory_service is mock_memory
        assert module._current_user_id == "user-123"
        assert module._current_session_id is None

    def test_sets_on_name_changed_callback(self):
        """Should set the on_name_changed callback."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        mock_callback = MagicMock()
        
        set_memory_service(
            mock_memory, "user-123", "session-456",
            on_name_changed=mock_callback
        )
        
        assert module._on_name_changed is mock_callback


class TestSetAgentNameCallback:
    """Tests for callback invocation on successful name change."""

    def test_calls_callback_on_success(self):
        """Should call on_name_changed callback when name stored."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        mock_memory.store_agent_name.return_value = True
        mock_callback = MagicMock()
        module._memory_service = mock_memory
        module._current_user_id = "test-user"
        module._current_session_id = "test-session"
        module._on_name_changed = mock_callback
        
        tool = {"toolUseId": "test-123", "input": {"name": "Bob"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "success"
        mock_callback.assert_called_once_with("test-user", "Bob")

    def test_does_not_call_callback_on_failure(self):
        """Should not call callback when storage fails."""
        import app.tools.set_agent_name as module
        mock_memory = MagicMock()
        mock_memory.store_agent_name.return_value = False
        mock_callback = MagicMock()
        module._memory_service = mock_memory
        module._current_user_id = "test-user"
        module._current_session_id = "test-session"
        module._on_name_changed = mock_callback
        
        tool = {"toolUseId": "test-123", "input": {"name": "Bob"}}
        
        result = set_agent_name(tool)
        
        assert result["status"] == "error"
        mock_callback.assert_not_called()
