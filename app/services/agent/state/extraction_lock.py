"""Extraction lock registry for preventing duplicate extractions."""


class ExtractionLockRegistry:
    """Manages extraction-in-progress locks per user.

    Prevents multiple extraction operations from running simultaneously
    for the same user.
    """

    def __init__(self) -> None:
        self._locks: dict[str, bool] = {}

    def acquire(self, user_id: str) -> bool:
        """Attempt to acquire an extraction lock for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            True if lock was acquired, False if already locked.
        """
        if self._locks.get(user_id, False):
            return False
        self._locks[user_id] = True
        return True

    def release(self, user_id: str) -> None:
        """Release extraction lock for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._locks.pop(user_id, None)

    def is_locked(self, user_id: str) -> bool:
        """Check if extraction is in progress for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            True if extraction is in progress, False otherwise.
        """
        return self._locks.get(user_id, False)

    def clear_all(self) -> None:
        """Clear all locks (useful for testing)."""
        self._locks.clear()
