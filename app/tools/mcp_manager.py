"""Built-in tools for managing MCP server configuration.

These tools allow the agent to add, remove, and list MCP servers
for the current user via the mcp_servers.json file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from strands import tool
except Exception:  # pragma: no cover
    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn
        return _decorator

if TYPE_CHECKING:
    from app.config import AgentConfig

logger = logging.getLogger(__name__)

_config: AgentConfig | None = None
_current_user_id: str | None = None
_repo_root: Path | None = None


def set_mcp_manager_context(
    config: AgentConfig,
    user_id: str,
    repo_root: Path,
) -> None:
    """Set the MCP service and user context for the tools.

    Called by agent_creation before creating the agent.

    Args:
        config: Application config.
        user_id: Current user's identifier.
        repo_root: Repository root path.
    """
    global _config, _current_user_id, _repo_root
    _config = config
    _current_user_id = user_id
    _repo_root = repo_root


@tool(
    name="mcp_add_server",
    description=(
        "Add an MCP (Model Context Protocol) server for the current user. "
        "This enables the agent to use additional tools provided by the MCP server. "
        "Provide a name, transport type ('remote' for SSE), and URL. "
        "Example: name='my-server', transport_type='remote', url='http://localhost:3000/sse'"
    ),
)
def mcp_add_server(
    name: str,
    transport_type: str,
    url: str,
) -> str:
    """Add an MCP server configuration.

    Args:
        name: A unique name for this server (e.g., 'my-mcp-server').
        transport_type: Transport type - use 'remote' for SSE connections.
        url: The server URL (e.g., 'http://localhost:3000/sse').

    Returns:
        Success message or error description.
    """
    from app.config import resolve_user_skills_dir
    from app.services.mcp.mcp_config import MCPServerConfig, load_mcp_config, save_mcp_config

    if _config is None or _current_user_id is None or _repo_root is None:
        return "MCP manager context not available."

    name = name.strip() if name else ""
    transport_type = transport_type.strip() if transport_type else ""
    url = url.strip() if url else ""

    if not name:
        return "Please provide a name for the MCP server."

    if transport_type != "remote":
        return "Currently only 'remote' transport type (SSE) is supported."

    if not url:
        return "Please provide a URL for the MCP server."

    try:
        user_skills_dir = resolve_user_skills_dir(_config, _current_user_id, create=True)
        config_path = user_skills_dir / "mcp_servers.json"

        # Load existing per-user config (don't merge global)
        existing = load_mcp_config(
            repo_root=_repo_root,
            user_id=None,
            user_skills_dir=user_skills_dir,
        )

        # Add or update server
        existing[name] = MCPServerConfig(
            name=name,
            server_type=transport_type,
            url=url,
        )

        save_mcp_config(config_path, existing)
        return (
            f"Added MCP server '{name}' with URL '{url}'. "
            "Start a new conversation to use the new tools."
        )

    except Exception as e:
        logger.exception("Failed to add MCP server")
        return f"Failed to add MCP server: {str(e)}"


@tool(
    name="mcp_remove_server",
    description=(
        "Remove an MCP server configuration for the current user. "
        "Use this to disable a previously added MCP server. "
        "Provide the server name that was used when adding it."
    ),
)
def mcp_remove_server(
    name: str,
) -> str:
    """Remove an MCP server configuration.

    Args:
        name: The name of the server to remove.

    Returns:
        Success message or error description.
    """
    from app.config import resolve_user_skills_dir
    from app.services.mcp.mcp_config import load_mcp_config, save_mcp_config

    if _config is None or _current_user_id is None or _repo_root is None:
        return "MCP manager context not available."

    name = name.strip() if name else ""

    if not name:
        return "Please provide the name of the MCP server to remove."

    try:
        user_skills_dir = resolve_user_skills_dir(_config, _current_user_id, create=True)
        config_path = user_skills_dir / "mcp_servers.json"

        existing = load_mcp_config(
            repo_root=_repo_root,
            user_id=None,
            user_skills_dir=user_skills_dir,
        )

        if name not in existing:
            return f"MCP server '{name}' not found for user {_current_user_id}."

        del existing[name]

        if not existing:
            # Remove file if empty
            if config_path.exists():
                config_path.unlink()
        else:
            save_mcp_config(config_path, existing)

        return (
            f"Removed MCP server '{name}'. "
            "Start a new conversation to apply changes."
        )

    except Exception as e:
        logger.exception("Failed to remove MCP server")
        return f"Failed to remove MCP server: {str(e)}"


@tool(
    name="mcp_list_servers",
    description=(
        "List all configured MCP servers for the current user. "
        "Use this to see which MCP servers are available and their connection details."
    ),
)
def mcp_list_servers() -> str:
    """List configured MCP servers.

    Returns:
        Formatted list of servers or message if none configured.
    """
    from app.config import resolve_user_skills_dir
    from app.services.mcp.mcp_config import load_mcp_config

    if _config is None or _current_user_id is None or _repo_root is None:
        return "MCP manager context not available."

    try:
        user_skills_dir = resolve_user_skills_dir(_config, _current_user_id, create=True)

        servers = load_mcp_config(
            repo_root=_repo_root,
            user_id=_current_user_id,
            user_skills_dir=user_skills_dir,
        )

        if not servers:
            return f"No MCP servers configured for user {_current_user_id}."

        lines = [f"MCP servers for user {_current_user_id}:\n"]

        for name, config in servers.items():
            lines.append(f"- **{name}** ({config.server_type})")
            if config.url:
                lines.append(f"  URL: {config.url}")
            if config.command:
                lines.append(f"  Command: {config.command}")
            if config.args:
                lines.append(f"  Args: {config.args}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Failed to list MCP servers")
        return f"Failed to list MCP servers: {str(e)}"
