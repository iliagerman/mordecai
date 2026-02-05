"""Short-term memory cache for Obsidian STM handoff."""

from collections.abc import MutableMapping


class StmCache(MutableMapping[str, str]):
    """Manages cached short-term memory content.

    Caches Obsidian STM scratchpad content for handoff between sessions.
    Enables clearing the on-disk file while preserving content for the
    next session's prompt injection.

    Implements MutableMapping for compatibility with existing code
    that expects dict-like behavior.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    # MutableMapping abstract methods
    def __getitem__(self, key: str) -> str:
        return self._cache[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._cache[key] = value

    def __delitem__(self, key: str) -> None:
        del self._cache[key]

    def __iter__(self):
        return iter(self._cache)

    def __len__(self) -> int:
        return len(self._cache)

    # Additional convenience methods
    def set(self, user_id: str, content: str) -> None:
        """Cache STM content for a user.

        Args:
            user_id: User's telegram ID.
            content: STM content to cache.
        """
        self._cache[user_id] = content

    def get(self, user_id: str, default: str | None = None) -> str | None:
        """Get cached STM content for a user.

        Args:
            user_id: User's telegram ID.
            default: Default value if not found.

        Returns:
            Cached STM content if exists, default otherwise.
        """
        return self._cache.get(user_id, default)

    def remove(self, user_id: str) -> None:
        """Remove cached content for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._cache.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all cached content (useful for testing)."""
        self._cache.clear()
