"""Unit tests for MCP config loading and saving.

Tests the MCP server configuration functionality including:
- Loading global and per-user MCP server configurations
- Saving MCP server configurations
- MCPServerConfig dataclass creation from dict
- Configuration override behavior (per-user overrides global)
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.mcp.mcp_config import (
    MCPServerConfig,
    load_mcp_config,
    save_mcp_config,
)


@pytest.fixture
def temp_repo_root():
    """Create a temporary directory to act as repo root."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_user_skills_dir():
    """Create a temporary directory for user skills."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestMCPServerConfig:
    """Tests for MCPServerConfig dataclass."""

    def test_from_dict_remote_server(self):
        """Should create MCPServerConfig from dict for remote server."""
        data = {
            "type": "remote",
            "url": "http://localhost:3000/sse",
        }
        config = MCPServerConfig.from_dict("test-server", data)

        assert config.name == "test-server"
        assert config.server_type == "remote"
        assert config.url == "http://localhost:3000/sse"
        assert config.command is None
        assert config.args is None

    def test_from_dict_stdio_server(self):
        """Should create MCPServerConfig from dict for stdio server."""
        data = {
            "type": "stdio",
            "command": "python",
            "args": ["server.py", "--port", "3000"],
        }
        config = MCPServerConfig.from_dict("local-server", data)

        assert config.name == "local-server"
        assert config.server_type == "stdio"
        assert config.command == "python"
        assert config.args == ["server.py", "--port", "3000"]
        assert config.url is None

    def test_from_dict_defaults_to_remote(self):
        """Should default to remote type when not specified."""
        data = {
            "url": "http://example.com/sse",
        }
        config = MCPServerConfig.from_dict("default-server", data)

        assert config.server_type == "remote"

    def test_from_dict_infers_stdio_when_command_present(self):
        """Should infer stdio type when command is present but type is omitted."""
        data = {
            "command": "npx",
            "args": ["-y", "vibe-kanban@latest", "--mcp"],
        }
        config = MCPServerConfig.from_dict("vibe_kanban", data)

        assert config.server_type == "stdio"
        assert config.command == "npx"
        assert config.args == ["-y", "vibe-kanban@latest", "--mcp"]

    def test_from_dict_explicit_type_overrides_inference(self):
        """Should use explicit type even when command is present."""
        data = {
            "type": "remote",
            "command": "npx",
            "url": "http://example.com/sse",
        }
        config = MCPServerConfig.from_dict("mixed-server", data)

        assert config.server_type == "remote"

    def test_from_dict_with_all_fields(self):
        """Should create MCPServerConfig with all fields."""
        data = {
            "type": "remote",
            "url": "http://localhost:3000/sse",
            "command": "node",
            "args": ["server.js"],
        }
        config = MCPServerConfig.from_dict("full-server", data)

        assert config.name == "full-server"
        assert config.server_type == "remote"
        assert config.url == "http://localhost:3000/sse"
        assert config.command == "node"
        assert config.args == ["server.js"]


class TestLoadMcpConfig:
    """Tests for load_mcp_config function."""

    def test_returns_empty_dict_when_no_config_files(self, temp_repo_root, temp_user_skills_dir):
        """Should return empty dict when no config files exist."""
        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        assert servers == {}

    def test_loads_global_config_only(self, temp_repo_root, temp_user_skills_dir):
        """Should load global config when only global exists."""
        global_config = {
            "mcp": {
                "homeserver-aws": {
                    "type": "remote",
                    "url": "http://mcp.homeserver/servers/aws/sse",
                },
                "homeserver-dev": {
                    "type": "remote",
                    "url": "http://mcp.homeserver/servers/dev/sse",
                },
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(global_config), encoding="utf-8"
        )

        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        assert len(servers) == 2
        assert "homeserver-aws" in servers
        assert "homeserver-dev" in servers
        assert servers["homeserver-aws"].url == "http://mcp.homeserver/servers/aws/sse"
        assert servers["homeserver-dev"].url == "http://mcp.homeserver/servers/dev/sse"

    def test_loads_user_config_only(self, temp_repo_root, temp_user_skills_dir):
        """Should load user config when only user config exists."""
        user_config = {
            "mcp": {
                "my-custom-server": {
                    "type": "remote",
                    "url": "http://localhost:3000/sse",
                }
            }
        }
        (temp_user_skills_dir / "mcp_servers.json").write_text(
            json.dumps(user_config), encoding="utf-8"
        )

        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        assert len(servers) == 1
        assert "my-custom-server" in servers
        assert servers["my-custom-server"].url == "http://localhost:3000/sse"

    def test_user_config_overrides_global(self, temp_repo_root, temp_user_skills_dir):
        """Should have user config override global config with same name."""
        global_config = {
            "mcp": {
                "shared-server": {
                    "type": "remote",
                    "url": "http://global.example.com/sse",
                }
            }
        }
        user_config = {
            "mcp": {
                "shared-server": {
                    "type": "remote",
                    "url": "http://user-specific.example.com/sse",
                }
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(global_config), encoding="utf-8"
        )
        (temp_user_skills_dir / "mcp_servers.json").write_text(
            json.dumps(user_config), encoding="utf-8"
        )

        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        assert len(servers) == 1
        assert "shared-server" in servers
        # User config should override global
        assert servers["shared-server"].url == "http://user-specific.example.com/sse"

    def test_merges_global_and_user_configs(self, temp_repo_root, temp_user_skills_dir):
        """Should merge global and user configs without conflict."""
        global_config = {
            "mcp": {
                "global-server": {
                    "type": "remote",
                    "url": "http://global.example.com/sse",
                }
            }
        }
        user_config = {
            "mcp": {
                "user-server": {
                    "type": "remote",
                    "url": "http://user.example.com/sse",
                }
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(global_config), encoding="utf-8"
        )
        (temp_user_skills_dir / "mcp_servers.json").write_text(
            json.dumps(user_config), encoding="utf-8"
        )

        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        assert len(servers) == 2
        assert "global-server" in servers
        assert "user-server" in servers
        assert servers["global-server"].url == "http://global.example.com/sse"
        assert servers["user-server"].url == "http://user.example.com/sse"

    def test_loads_stdio_server_config(self, temp_repo_root):
        """Should load stdio server configuration."""
        config = {
            "mcp": {
                "local-python": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "mcp_server"],
                }
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

        servers = load_mcp_config(repo_root=temp_repo_root)

        assert len(servers) == 1
        assert "local-python" in servers
        assert servers["local-python"].server_type == "stdio"
        assert servers["local-python"].command == "python"
        assert servers["local-python"].args == ["-m", "mcp_server"]

    def test_handles_malformed_global_config_gracefully(self, temp_repo_root, temp_user_skills_dir, caplog):
        """Should log warning and continue when global config is malformed."""
        (temp_repo_root / "mcp_servers.json").write_text("invalid json", encoding="utf-8")

        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        assert servers == {}
        # Should log a warning about the failure

    def test_handles_malformed_user_config_gracefully(self, temp_repo_root, temp_user_skills_dir, caplog):
        """Should log warning and load global when user config is malformed."""
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
        (temp_user_skills_dir / "mcp_servers.json").write_text("invalid json", encoding="utf-8")

        servers = load_mcp_config(
            repo_root=temp_repo_root,
            user_id="test-user",
            user_skills_dir=temp_user_skills_dir,
        )

        # Should still load global config
        assert len(servers) == 1
        assert "global-server" in servers

    def test_loads_config_without_user_id(self, temp_repo_root):
        """Should load only global config when user_id is None."""
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

        servers = load_mcp_config(repo_root=temp_repo_root, user_id=None)

        assert len(servers) == 1
        assert "global-server" in servers

    def test_skips_non_dict_entries(self, temp_repo_root):
        """Should skip non-dict entries in mcp section."""
        config = {
            "mcp": {
                "valid-server": {
                    "type": "remote",
                    "url": "http://example.com/sse",
                },
                "invalid-entry": "not a dict",
                123: "numeric key",
            }
        }
        (temp_repo_root / "mcp_servers.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

        servers = load_mcp_config(repo_root=temp_repo_root)

        assert len(servers) == 1
        assert "valid-server" in servers


class TestSaveMcpConfig:
    """Tests for save_mcp_config function."""

    def test_saves_config_to_file(self, temp_repo_root):
        """Should save config to file."""
        config_path = temp_repo_root / "mcp_servers.json"
        servers = {
            "test-server": MCPServerConfig(
                name="test-server",
                server_type="remote",
                url="http://localhost:3000/sse",
            )
        }

        save_mcp_config(config_path, servers)

        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "mcp" in data
        assert "test-server" in data["mcp"]
        assert data["mcp"]["test-server"]["type"] == "remote"
        assert data["mcp"]["test-server"]["url"] == "http://localhost:3000/sse"

    def test_creates_parent_directory(self, temp_repo_root):
        """Should create parent directory if it doesn't exist."""
        config_path = temp_repo_root / "nested" / "dir" / "mcp_servers.json"
        servers = {
            "test-server": MCPServerConfig(
                name="test-server",
                server_type="remote",
                url="http://localhost:3000/sse",
            )
        }

        save_mcp_config(config_path, servers)

        assert config_path.exists()
        assert config_path.parent.is_dir()

    def test_saves_multiple_servers(self, temp_repo_root):
        """Should save multiple servers to file."""
        config_path = temp_repo_root / "mcp_servers.json"
        servers = {
            "server-1": MCPServerConfig(
                name="server-1",
                server_type="remote",
                url="http://server1.example.com/sse",
            ),
            "server-2": MCPServerConfig(
                name="server-2",
                server_type="stdio",
                command="python",
                args=["-m", "server"],
            ),
        }

        save_mcp_config(config_path, servers)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert len(data["mcp"]) == 2
        assert data["mcp"]["server-1"]["type"] == "remote"
        assert data["mcp"]["server-1"]["url"] == "http://server1.example.com/sse"
        assert data["mcp"]["server-2"]["type"] == "stdio"
        assert data["mcp"]["server-2"]["command"] == "python"
        assert data["mcp"]["server-2"]["args"] == ["-m", "server"]

    def test_saves_server_with_optional_fields(self, temp_repo_root):
        """Should save server with all optional fields."""
        config_path = temp_repo_root / "mcp_servers.json"
        servers = {
            "full-server": MCPServerConfig(
                name="full-server",
                server_type="stdio",
                command="node",
                args=["server.js", "--port", "3000"],
                url="http://localhost:3000/sse",  # Included even for stdio
            )
        }

        save_mcp_config(config_path, servers)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        server_data = data["mcp"]["full-server"]
        assert server_data["type"] == "stdio"
        assert server_data["command"] == "node"
        assert server_data["args"] == ["server.js", "--port", "3000"]
        assert server_data["url"] == "http://localhost:3000/sse"

    def test_saves_server_without_optional_fields(self, temp_repo_root):
        """Should save server with only required fields."""
        config_path = temp_repo_root / "mcp_servers.json"
        servers = {
            "minimal-server": MCPServerConfig(
                name="minimal-server",
                server_type="remote",
                url="http://localhost:3000/sse",
            )
        }

        save_mcp_config(config_path, servers)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        server_data = data["mcp"]["minimal-server"]
        assert server_data == {"type": "remote", "url": "http://localhost:3000/sse"}
        assert "command" not in server_data
        assert "args" not in server_data

    def test_overwrites_existing_file(self, temp_repo_root):
        """Should overwrite existing config file."""
        config_path = temp_repo_root / "mcp_servers.json"
        # Write initial config
        initial_data = {"mcp": {"old-server": {"type": "remote", "url": "http://old.com/sse"}}}
        config_path.write_text(json.dumps(initial_data), encoding="utf-8")

        # Save new config
        servers = {
            "new-server": MCPServerConfig(
                name="new-server",
                server_type="remote",
                url="http://new.com/sse",
            )
        }
        save_mcp_config(config_path, servers)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "old-server" not in data["mcp"]
        assert "new-server" in data["mcp"]

    def test_saves_empty_servers_dict(self, temp_repo_root):
        """Should save empty servers dict (creates empty mcp section)."""
        config_path = temp_repo_root / "mcp_servers.json"

        save_mcp_config(config_path, {})

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data == {"mcp": {}}
