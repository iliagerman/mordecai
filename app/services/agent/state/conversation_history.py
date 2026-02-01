"""Conversation history tracker for memory extraction."""

from app.models.agent import ConversationMessage


class ConversationHistory:
    """Manages conversation history for memory extraction.

    Tracks messages exchanged during a session for later extraction
    into long-term memory when the session ends or message limit is reached.
    """

    def __init__(self) -> None:
        self._history: dict[str, list[ConversationMessage]] = {}

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """Add a message to the conversation history.

        Args:
            user_id: User's telegram ID.
            role: Message role ('user' or 'assistant').
            content: Message content.
        """
        if user_id not in self._history:
            self._history[user_id] = []
        self._history[user_id].append(
            ConversationMessage(role=role, content=content)
        )

    def get(self, user_id: str) -> list[ConversationMessage]:
        """Get conversation history for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            List of messages from the user's current session.
        """
        return self._history.get(user_id, [])

    def clear(self, user_id: str) -> None:
        """Clear conversation history for a user.

        Args:
            user_id: User's telegram ID.

        Note:
            Keeps the key present but reset to an empty list for consistency
            with unit tests that assert the cleared state is [] (not missing/None).
        """
        self._history[user_id] = []

    def remove(self, user_id: str) -> None:
        """Remove conversation history for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._history.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all history (useful for testing)."""
        self._history.clear()

    def to_dict_list(self, user_id: str) -> list[dict]:
        """Convert history to list of dicts for backward compatibility.

        Args:
            user_id: User's telegram ID.

        Returns:
            List of message dicts with role and content.
        """
        messages = self.get(user_id)
        return [msg.model_dump() for msg in messages]
