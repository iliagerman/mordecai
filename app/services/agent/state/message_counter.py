"""Message counter for tracking user message counts."""


class MessageCounter:
    """Manages message counts per user.

    Tracks the number of messages exchanged in a session for
    triggering extraction when limits are reached.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def increment(self, user_id: str, count: int = 1) -> int:
        """Increment and return the message count for a user.

        Args:
            user_id: User's telegram ID.
            count: Number to increment by (default 1).

        Returns:
            Updated message count for the user.
        """
        current = self._counts.get(user_id, 0)
        self._counts[user_id] = current + count
        return self._counts[user_id]

    def get(self, user_id: str) -> int:
        """Get current message count for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            Current message count (0 if user has no messages).
        """
        return self._counts.get(user_id, 0)

    def reset(self, user_id: str) -> None:
        """Reset message count to zero for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._counts[user_id] = 0

    def remove(self, user_id: str) -> None:
        """Remove count for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._counts.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all counts (useful for testing)."""
        self._counts.clear()
