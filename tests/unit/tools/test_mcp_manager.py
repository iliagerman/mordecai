"""Unit tests for MCP manager tools.

Tests the MCP server management tools including:
- mcp_add_server validation and success cases
- mcp_remove_server functionality
- mcp_list_servers functionality
- set_mcp_manager_context function
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import AgentConfig
from app.tools import mcp_manager as mcp_manager_module


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    mcp_manager_module._config = None
    mcp_manager_module._current_user_id = None
    mcp_manager_module._repo_root = None
    yield
    mcp_manager_module._config = None
    mcp_manager_module._current_user_id = None
    mcp_manager_module._repo_root = None


@pytest.fixture
def agent_config(tmp_path: Path):
    """Create a real AgentConfig with temp directories."""
    config = AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=str(tmp_path / "skills"),
        shared_skills_dir=str(tmp_path / "shared"),
    )
    return config


@pytest.fixture
def temp_repo_root(tmp_path: Path):
    """Create a temporary repo root directory."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    return repo_root


class TestSetMcpManagerContext:
    """Tests for set_mcp_manager_context function."""

    def test_sets_config_and_user_id_and_repo_root(self, agent_config, temp_repo_root):
        """Should set config, user_id, and repo_root."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        assert mcp_manager_module._config is agent_config
        assert mcp_manager_module._current_user_id == "user-123"
        assert mcp_manager_module._repo_root == temp_repo_root


class TestMcpAddServer:
    """Tests for mcp_add_server tool function."""

    def test_returns_error_when_name_empty(self, agent_config, temp_repo_root):
        """Should return error when name is empty."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_add_server(
            name="",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        assert "Please provide a name" in result

    def test_returns_error_when_name_whitespace(self, agent_config, temp_repo_root):
        """Should return error when name is only whitespace."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_add_server(
            name="   ",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        assert "Please provide a name" in result

    def test_returns_error_when_transport_type_not_remote(self, agent_config, temp_repo_root):
        """Should return error when transport type is not 'remote'."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_add_server(
            name="test-server",
            transport_type="stdio",
            url="http://localhost:3000/sse",
        )

        assert "Currently only 'remote' transport type" in result

    def test_returns_error_when_url_empty(self, agent_config, temp_repo_root):
        """Should return error when URL is empty."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_add_server(
            name="test-server",
            transport_type="remote",
            url="",
        )

        assert "Please provide a URL" in result

    def test_returns_error_when_context_not_set(self):
        """Should return error when context is not available."""
        result = mcp_manager_module.mcp_add_server(
            name="test-server",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        assert "MCP manager context not available" in result

    def test_adds_server_to_user_config(self, agent_config, temp_repo_root):
        """Should add server to user's mcp_servers.json."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_add_server(
            name="my-server",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        assert "Added MCP server 'my-server'" in result
        assert "http://localhost:3000/sse" in result

        # Verify file was created
        user_skills_dir = Path(agent_config.skills_base_dir) / "user-123"
        config_path = user_skills_dir / "mcp_servers.json"
        assert config_path.exists()

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "my-server" in data["mcp"]
        assert data["mcp"]["my-server"]["type"] == "remote"
        assert data["mcp"]["my-server"]["url"] == "http://localhost:3000/sse"

    def test_updates_existing_server(self, agent_config, temp_repo_root):
        """Should update existing server with same name."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        # Add initial server
        mcp_manager_module.mcp_add_server(
            name="my-server",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        # Update with new URL
        result = mcp_manager_module.mcp_add_server(
            name="my-server",
            transport_type="remote",
            url="http://localhost:4000/sse",
        )

        assert "Added MCP server 'my-server'" in result

        # Verify URL was updated
        user_skills_dir = Path(agent_config.skills_base_dir) / "user-123"
        config_path = user_skills_dir / "mcp_servers.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcp"]["my-server"]["url"] == "http://localhost:4000/sse"


class TestMcpRemoveServer:
    """Tests for mcp_remove_server tool function."""

    def test_returns_error_when_name_empty(self, agent_config, temp_repo_root):
        """Should return error when name is empty."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_remove_server(name="")

        assert "Please provide the name" in result

    def test_returns_error_when_name_whitespace(self, agent_config, temp_repo_root):
        """Should return error when name is only whitespace."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_remove_server(name="   ")

        assert "Please provide the name" in result

    def test_returns_error_when_context_not_set(self):
        """Should return error when context is not available."""
        result = mcp_manager_module.mcp_remove_server(name="test-server")

        assert "MCP manager context not available" in result

    def test_returns_error_when_server_not_found(self, agent_config, temp_repo_root):
        """Should return error when server is not found."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_remove_server(name="nonexistent")

        assert "not found for user user-123" in result

    def test_removes_server_from_user_config(self, agent_config, temp_repo_root):
        """Should remove server from user's mcp_servers.json."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        # First add a server
        mcp_manager_module.mcp_add_server(
            name="my-server",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        # Then remove it
        result = mcp_manager_module.mcp_remove_server(name="my-server")

        assert "Removed MCP server 'my-server'" in result

        # Verify file was removed or is empty
        user_skills_dir = Path(agent_config.skills_base_dir) / "user-123"
        config_path = user_skills_dir / "mcp_servers.json"
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            assert "my-server" not in data.get("mcp", {})

    def test_removes_config_file_when_last_server_removed(self, agent_config, temp_repo_root):
        """Should remove config file when last server is removed."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        # Add and then remove the only server
        mcp_manager_module.mcp_add_server(
            name="only-server",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        mcp_manager_module.mcp_remove_server(name="only-server")

        # Verify file was removed
        user_skills_dir = Path(agent_config.skills_base_dir) / "user-123"
        config_path = user_skills_dir / "mcp_servers.json"
        assert not config_path.exists()

    def test_keeps_other_servers_when_removing_one(self, agent_config, temp_repo_root):
        """Should keep other servers when removing one."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        # Add two servers
        mcp_manager_module.mcp_add_server(
            name="server-1",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )
        mcp_manager_module.mcp_add_server(
            name="server-2",
            transport_type="remote",
            url="http://localhost:4000/sse",
        )

        # Remove one
        mcp_manager_module.mcp_remove_server(name="server-1")

        # Verify server-2 still exists
        user_skills_dir = Path(agent_config.skills_base_dir) / "user-123"
        config_path = user_skills_dir / "mcp_servers.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "server-1" not in data["mcp"]
        assert "server-2" in data["mcp"]


class TestMcpListServers:
    """Tests for mcp_list_servers tool function."""

    def test_returns_error_when_context_not_set(self):
        """Should return error when context is not available."""
        result = mcp_manager_module.mcp_list_servers()

        assert "MCP manager context not available" in result

    def test_returns_no_servers_message_when_none_configured(self, agent_config, temp_repo_root):
        """Should return message when no servers are configured."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_list_servers()

        assert "No MCP servers configured for user user-123" in result

    def test_lists_user_servers(self, agent_config, temp_repo_root):
        """Should list user's configured servers."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        # Add servers
        mcp_manager_module.mcp_add_server(
            name="server-1",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )
        mcp_manager_module.mcp_add_server(
            name="server-2",
            transport_type="remote",
            url="http://localhost:4000/sse",
        )

        result = mcp_manager_module.mcp_list_servers()

        assert "MCP servers for user user-123" in result
        assert "**server-1**" in result
        assert "**server-2**" in result
        assert "http://localhost:3000/sse" in result
        assert "http://localhost:4000/sse" in result

    def test_lists_global_servers_for_user(self, agent_config, temp_repo_root):
        """Should include global servers in list for user."""
        # Create global config
        global_config = {
            "mcp": {
                "global-server": {
                    "type": "remote",
                    "url": "http://global.example.com/sse",
                }
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(global_config), encoding="utf-8"
        )

        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        result = mcp_manager_module.mcp_list_servers()

        assert "MCP servers for user user-123" in result
        assert "**global-server**" in result
        assert "http://global.example.com/sse" in result

    def test_shows_user_override_of_global_server(self, agent_config, temp_repo_root):
        """Should show user's overridden URL for global server."""
        # Create global config
        global_config = {
            "mcp": {
                "shared-server": {
                    "type": "remote",
                    "url": "http://global.example.com/sse",
                }
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(global_config), encoding="utf-8"
        )

        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        # Override with user config
        mcp_manager_module.mcp_add_server(
            name="shared-server",
            transport_type="remote",
            url="http://user-specific.example.com/sse",
        )

        result = mcp_manager_module.mcp_list_servers()

        # Should show the user's override URL
        assert "http://user-specific.example.com/sse" in result
        assert "http://global.example.com/sse" not in result

    def test_shows_transport_type(self, agent_config, temp_repo_root):
        """Should show transport type for each server."""
        mcp_manager_module.set_mcp_manager_context(agent_config, "user-123", temp_repo_root)

        mcp_manager_module.mcp_add_server(
            name="my-server",
            transport_type="remote",
            url="http://localhost:3000/sse",
        )

        result = mcp_manager_module.mcp_list_servers()

        assert "(remote)" in result
