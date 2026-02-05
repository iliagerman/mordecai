"""Tool for searching long-term memory.

This tool allows the agent to search for facts, preferences, and summaries
stored in AgentCore Memory when the user asks about past conversations
or stored information.
"""

from typing import Literal

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


# Global reference to memory service - set by agent_service
_memory_service = None
_current_user_id = None


def set_memory_context(memory_service, user_id: str) -> None:
    """Set the memory service and user ID for the tool.

    Called by agent_service before creating the agent.
    """
    global _memory_service, _current_user_id
    _memory_service = memory_service
    _current_user_id = user_id


@tool(
    name="search_memory",
    description=(
        "Search your long-term memory for information about the user. "
        "Use this tool when the user asks about past conversations, "
        "their preferences, facts you've learned about them, or anything "
        "they've told you before. Examples: 'what do you know about me?', "
        "'what are my preferences?', 'do you remember when I told you...', "
        "'what have we discussed before?'"
    ),
)
def search_memory(
    query: str,
    memory_type: Literal["all", "facts", "preferences"] = "all",
) -> str:
    """Search long-term memory for relevant information.

    Args:
        query: The search query to find relevant memories.
        memory_type: Type of memory to search: 'facts' for learned
            information, 'preferences' for user preferences,
            or 'all' for both. Default is 'all'.

    Returns:
        Found memories or message if none found.
    """
    query = query.strip() if query else ""

    if not query:
        return "No search query provided."

    if _memory_service is None:
        return (
            "Memory service not available. "
            "I cannot search my long-term memory right now."
        )

    if _current_user_id is None:
        return "User context not available."

    try:
        results = _memory_service.search_memory(
            user_id=_current_user_id,
            query=query,
            memory_type=memory_type
        )

        facts = results.get("facts", [])
        preferences = results.get("preferences", [])

        if not facts and not preferences:
            return (
                f"No memories found matching '{query}'. "
                "I don't have any stored information about this."
            )

        # Format results
        response_parts = []

        if facts:
            response_parts.append("**Facts I remember:**")
            for fact in facts:
                response_parts.append(f"- {fact}")

        if preferences:
            if facts:
                response_parts.append("")  # blank line
            response_parts.append("**Your preferences:**")
            for pref in preferences:
                response_parts.append(f"- {pref}")

        return "\n".join(response_parts)

    except Exception as e:
        return f"Error searching memory: {str(e)}"
