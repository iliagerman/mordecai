"""Session manager for tracking user sessions."""

from datetime import datetime


class SessionManager:
    """Manages user session IDs.

    Each user has a unique session ID that changes when starting a new session.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    def get_or_create(self, user_id: str) -> str:
        """Get existing session ID or create a new one.

        Args:
            user_id: User's telegram ID.

        Returns:
            Session ID for the user.
        """
        if user_id not in self._sessions:
            self.create_new(user_id)
        return self._sessions[user_id]

    def create_new(self, user_id: str) -> str:
        """Create a new session ID for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            New session ID.
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        self._sessions[user_id] = f"session_{user_id}_{timestamp}"
        return self._sessions[user_id]

    def set(self, user_id: str, session_id: str) -> None:
        """Restore a previously-known session ID for a user.

        Used to recover the session after a process restart by replaying
        the session_id persisted in the conversation database.

        Args:
            user_id: User's telegram ID.
            session_id: Session ID to restore.
        """
        self._sessions[user_id] = session_id

    def get(self, user_id: str) -> str | None:
        """Get session ID for a user without creating one.

        Args:
            user_id: User's telegram ID.

        Returns:
            Session ID if exists, None otherwise.
        """
        return self._sessions.get(user_id)

    def remove(self, user_id: str) -> None:
        """Remove session for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._sessions.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all sessions (useful for testing)."""
        self._sessions.clear()
