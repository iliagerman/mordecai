"""Agent name registry for caching user agent names."""

from collections.abc import MutableMapping
from typing import cast


class AgentNameRegistry(MutableMapping[str, str | None]):
    """Manages cached agent names for users.

    Agent names are stored in the database per user, but cached
    in-memory for performance during active sessions.

    Implements MutableMapping for compatibility with existing code
    that expects dict-like behavior.
    """

    def __init__(self) -> None:
        self._names: dict[str, str | None] = {}

    # MutableMapping abstract methods
    def __getitem__(self, key: str) -> str | None:
        return self._names[key]

    def __setitem__(self, key: str, value: str | None) -> None:
        self._names[key] = value

    def __delitem__(self, key: str) -> None:
        del self._names[key]

    def __iter__(self):
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)

    # Additional convenience methods
    def set(self, user_id: str, name: str) -> None:
        """Set the agent name for a user.

        Args:
            user_id: User's telegram ID.
            name: Name to assign to the agent.
        """
        self._names[user_id] = name

    def get(self, user_id: str, default: str | None = None) -> str | None:
        """Get the cached agent name for a user.

        Args:
            user_id: User's telegram ID.
            default: Default value if not found.

        Returns:
            Agent name if cached, default otherwise.
        """
        return self._names.get(user_id, default)

    def remove(self, user_id: str) -> None:
        """Remove cached name for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._names.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all cached names (useful for testing)."""
        self._names.clear()
