"""Tool for setting the agent's name in memory.

This tool allows the agent to explicitly store its name when a user
assigns one, ensuring it persists across sessions.
"""

from typing import Any, Callable

TOOL_SPEC = {
    "name": "set_agent_name",
    "description": (
        "Store your name in memory when a user gives you a name. "
        "Use this tool whenever a user says something like 'your name is X', "
        "'I'll call you X', 'call yourself X', or assigns you any name. "
        "This ensures you remember the name across sessions."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name the user wants to call you"
                }
            },
            "required": ["name"]
        }
    }
}


# Global reference to memory service - set by agent_service
_memory_service = None
_current_user_id = None
_current_session_id = None
_on_name_changed: Callable[[str, str], None] | None = None


def set_memory_service(
    memory_service,
    user_id: str,
    session_id: str | None = None,
    on_name_changed: Callable[[str, str], None] | None = None
) -> None:
    """Set the memory service and user ID for the tool.

    Called by agent_service before creating the agent.

    Args:
        memory_service: The memory service instance.
        user_id: Current user ID.
        session_id: Current session ID.
        on_name_changed: Callback when name is successfully changed.
            Called with (user_id, new_name).
    """
    global _memory_service, _current_user_id, _current_session_id
    global _on_name_changed
    _memory_service = memory_service
    _current_user_id = user_id
    _current_session_id = session_id
    _on_name_changed = on_name_changed


def set_agent_name(tool: dict, **kwargs: Any) -> dict:
    """Store the agent's name in memory.

    Args:
        tool: Tool invocation data with toolUseId and input.
        **kwargs: Additional context.

    Returns:
        Tool result with success/error status.
    """
    tool_use_id = tool["toolUseId"]
    tool_input = tool.get("input", {})
    name = tool_input.get("name", "").strip()

    if not name:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "No name provided. Please specify a name."}]
        }

    if _memory_service is None:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "Memory service not available."}]
        }

    if _current_user_id is None:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "User context not available."}]
        }

    if _current_session_id is None:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "Session context not available."}]
        }

    # Store the name in memory via create_event
    success = _memory_service.store_agent_name(
        _current_user_id, name, _current_session_id
    )

    if success:
        # Notify agent service to update its cache
        if _on_name_changed is not None:
            _on_name_changed(_current_user_id, name)
        return {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{
                "text": f"I've stored my name as '{name}' in memory. "
                        f"I'll remember this name across our conversations."
            }]
        }
    else:
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{
                "text": f"Failed to store name '{name}' in long-term memory. "
                        f"Memory storage is not available. "
                        f"I will use the name '{name}' for this session only, "
                        f"but I won't remember it in future sessions."
            }]
        }
