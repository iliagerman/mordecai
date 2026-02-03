"""MCP server configuration loading and saving.

This module provides functions to load and save MCP server configurations
from mcp_servers.json files, with support for global and per-user overrides.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Configuration for a single MCP server.

    Attributes:
        name: Unique identifier for this server.
        server_type: Transport type - "remote" (SSE) or "stdio".
        url: URL for SSE transport (remote servers).
        command: Command for stdio transport (local processes).
        args: Arguments for stdio transport command.
    """

    name: str
    server_type: str  # "remote" (SSE) or "stdio"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> MCPServerConfig:
        """Create MCPServerConfig from dictionary.

        Args:
            name: Server name.
            data: Dictionary with server configuration.

        Returns:
            MCPServerConfig instance.
        """
        return cls(
            name=name,
            server_type=data.get("type", "remote"),
            url=data.get("url"),
            command=data.get("command"),
            args=data.get("args"),
        )


def load_mcp_config(
    repo_root: Path,
    user_id: str | None = None,
    user_skills_dir: Path | None = None,
) -> dict[str, MCPServerConfig]:
    """Load MCP server configuration.

    Load order (later overrides earlier):
    1. {repo_root}/mcp_servers.json (global)
    2. {user_skills_dir}/mcp_servers.json (per-user)

    Args:
        repo_root: Repository root directory.
        user_id: Optional user ID for per-user overrides.
        user_skills_dir: Optional user skills directory path.

    Returns:
        Dict mapping server names to their configurations.
    """
    servers: dict[str, MCPServerConfig] = {}

    # Load global config
    global_config_path = repo_root / "mcp_servers.json"
    if global_config_path.exists():
        try:
            data = json.loads(global_config_path.read_text(encoding="utf-8"))
            mcp_section = data.get("mcp", {})
            for name, server_data in mcp_section.items():
                if isinstance(server_data, dict):
                    servers[name] = MCPServerConfig.from_dict(name, server_data)
                    logger.debug("Loaded global MCP server '%s'", name)
        except Exception as e:
            logger.warning("Failed to load global MCP config: %s", e)

    # Load per-user override
    if user_id and user_skills_dir:
        user_config_path = user_skills_dir / "mcp_servers.json"
        if user_config_path.exists():
            try:
                data = json.loads(user_config_path.read_text(encoding="utf-8"))
                mcp_section = data.get("mcp", {})
                for name, server_data in mcp_section.items():
                    if isinstance(server_data, dict):
                        servers[name] = MCPServerConfig.from_dict(name, server_data)
                        logger.debug("Loaded user %s MCP server '%s'", user_id, name)
            except Exception as e:
                logger.warning("Failed to load user %s MCP config: %s", user_id, e)

    return servers


def load_mcp_config_file(config_path: Path) -> dict[str, MCPServerConfig]:
    """Load MCP server configuration from a single file.

    This is used for per-user mutations (add/remove) so we don't accidentally
    materialize merged global servers into the user's override file.
    """

    servers: dict[str, MCPServerConfig] = {}
    if not config_path.exists():
        return servers

    data = json.loads(config_path.read_text(encoding="utf-8"))
    mcp_section = data.get("mcp", {})
    if not isinstance(mcp_section, dict):
        return servers

    for name, server_data in mcp_section.items():
        if isinstance(server_data, dict):
            servers[name] = MCPServerConfig.from_dict(name, server_data)
    return servers


def save_mcp_config(
    config_path: Path,
    servers: dict[str, MCPServerConfig],
) -> None:
    """Save MCP server configuration to file.

    Args:
        config_path: Path to save the configuration file.
        servers: Dict of server configurations to save.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    mcp_section: dict[str, dict[str, Any]] = {}
    for name, server in servers.items():
        server_dict: dict[str, Any] = {"type": server.server_type}
        if server.url:
            server_dict["url"] = server.url
        if server.command:
            server_dict["command"] = server.command
        if server.args:
            server_dict["args"] = server.args
        mcp_section[name] = server_dict

    data = {"mcp": mcp_section}
    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Saved MCP config to %s with %d servers", config_path, len(servers))
