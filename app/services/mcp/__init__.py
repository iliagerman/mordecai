"""MCP (Model Context Protocol) service package.

This package provides configuration and tool loading support for MCP servers,
enabling the agent to dynamically load tools from external MCP servers.
"""

from app.services.mcp.mcp_config import (
    MCPServerConfig,
    load_mcp_config,
    save_mcp_config,
)

__all__ = [
    "MCPServerConfig",
    "load_mcp_config",
    "save_mcp_config",
]
