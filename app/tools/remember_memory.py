"""Tools for explicitly storing user-provided memory.

These tools let the agent store a fact or preference immediately when a
user explicitly asks it to "remember" something.

We keep these as explicit tools (instead of relying only on end-of-session
memory extraction) so "remember X" requests persist right away.
"""

from __future__ import annotations

from typing import Any, Literal

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


_memory_service: Any = None
_current_user_id: str | None = None
_current_session_id: str | None = None


def set_memory_context(memory_service, user_id: str, session_id: str) -> None:
    """Set memory context for this module's tools.

    Called by agent_service before creating the agent.
    """

    global _memory_service, _current_user_id, _current_session_id
    _memory_service = memory_service
    _current_user_id = user_id
    _current_session_id = session_id


def _require_context() -> tuple[bool, str]:
    if _memory_service is None:
        return False, "Memory service not available."
    if _current_user_id is None:
        return False, "User context not available."
    if _current_session_id is None:
        return False, "Session context not available."
    return True, ""


@tool(
    name="remember_fact",
    description=(
        "Store an explicit fact in long-term memory. "
        "Use this when the user says to remember something for later. "
        "Example: 'Remember we keep the shopping lists in the family vault.'"
    ),
)
def remember_fact(
    fact: str,
    replace_similar: bool = True,
) -> str:
    """Store a fact in long-term memory."""

    ok, err = _require_context()
    if not ok:
        return err

    fact = (fact or "").strip()
    if not fact:
        return "No fact provided."

    try:
        success = _memory_service.store_fact(
            user_id=_current_user_id,
            fact=fact,
            session_id=_current_session_id,
            replace_similar=replace_similar,
            similarity_query=fact,
        )
        if success:
            return "Saved."
        return "Failed to save to long-term memory."
    except Exception as e:
        return f"Failed to save to long-term memory: {e}"


@tool(
    name="remember_preference",
    description=(
        "Store an explicit user preference in long-term memory. "
        "Use this when the user says to remember a preference or setting."
    ),
)
def remember_preference(preference: str) -> str:
    """Store a preference in long-term memory."""

    ok, err = _require_context()
    if not ok:
        return err

    preference = (preference or "").strip()
    if not preference:
        return "No preference provided."

    # Not all deployments implement preference storage yet.
    if not hasattr(_memory_service, "store_preference"):
        # Fall back to storing as a fact.
        try:
            success = _memory_service.store_fact(
                user_id=_current_user_id,
                fact=f"User preference: {preference}",
                session_id=_current_session_id,
                replace_similar=True,
                similarity_query=preference,
            )
            return "Saved." if success else "Failed to save to long-term memory."
        except Exception as e:
            return f"Failed to save to long-term memory: {e}"

    try:
        success = _memory_service.store_preference(
            user_id=_current_user_id,
            preference=preference,
            session_id=_current_session_id,
        )
        return "Saved." if success else "Failed to save to long-term memory."
    except Exception as e:
        return f"Failed to save to long-term memory: {e}"


@tool(
    name="remember",
    description=(
        "Store something the user explicitly asked you to remember. "
        "Set memory_type to 'fact' or 'preference'."
    ),
)
def remember(
    text: str,
    memory_type: Literal["fact", "preference"] = "fact",
) -> str:
    """Convenience wrapper for remember_fact/remember_preference."""

    if memory_type == "preference":
        return remember_preference(text)
    return remember_fact(text)
