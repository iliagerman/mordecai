"""DAO for conversation message persistence.

This module provides database access for storing and retrieving conversation
messages, enabling conversation recovery across server restarts and isolating
cron task messages from the main user conversation.

Requirements:
- Persist conversation messages to database
- Support loading conversation history for agent initialization
- Isolate cron task messages (is_cron=True) from main conversation
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, func
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ConversationDAO:
    """DAO for conversation message persistence.

    Provides methods for saving and loading conversation messages,
    with support for excluding cron task messages from the main
    conversation history.
    """

    def __init__(self, session_factory: callable) -> None:
        """Initialize the ConversationDAO.

        Args:
            session_factory: Async callable that returns an AsyncSession context manager.
                              Expects: async with session_factory() as session: ...
        """
        self._session_factory = session_factory

    async def save_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        is_cron: bool = False,
        created_at: datetime | None = None,
    ) -> bool:
        """Save a conversation message to the database.

        Args:
            user_id: User's ID.
            session_id: Session identifier for grouping messages.
            role: Message role ('user' or 'assistant').
            content: Message content.
            is_cron: If True, marks this as a cron task message.
            created_at: Optional timestamp (defaults to current time).

        Returns:
            True if save was successful, False otherwise.
        """
        from app.models.orm import ConversationMessageModel

        async with self._session_factory() as session:
            try:
                msg = ConversationMessageModel(
                    user_id=user_id,
                    session_id=session_id,
                    role=role,
                    content=content,
                    is_cron=is_cron,
                    created_at=created_at or datetime.utcnow(),
                )
                session.add(msg)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save conversation message: %s", e)
                return False

    async def get_conversation(
        self,
        user_id: str,
        session_id: str | None = None,
        exclude_cron: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Load conversation history from the database.

        Args:
            user_id: User's ID.
            session_id: Optional session filter. If None, loads all sessions.
            exclude_cron: If True, excludes cron task messages (default: True).
            limit: Optional max messages to return (most recent first).

        Returns:
            List of message dicts with role and content, ordered by creation time.
        """
        from app.models.orm import ConversationMessageModel

        async with self._session_factory() as session:
            try:
                query = select(ConversationMessageModel).where(
                    ConversationMessageModel.user_id == user_id
                )

                if session_id:
                    query = query.where(ConversationMessageModel.session_id == session_id)

                if exclude_cron:
                    query = query.where(ConversationMessageModel.is_cron == False)

                query = query.order_by(ConversationMessageModel.created_at.asc())

                if limit:
                    # For limit, we want the most recent messages, so we need
                    # to order differently and then reverse
                    query = query.order_by(ConversationMessageModel.created_at.desc())
                    query = query.limit(limit)

                result = await session.execute(query)
                messages = result.scalars().all()

                # If we used desc() for limit, reverse back to chronological order
                if limit:
                    messages = list(reversed(messages))

                return [
                    {"role": m.role, "content": m.content}
                    for m in messages
                ]
            except SQLAlchemyError as e:
                logger.error("Failed to load conversation: %s", e)
                return []

    async def clear_conversation(
        self,
        user_id: str,
        session_id: str | None = None,
        clear_cron_only: bool = False,
    ) -> int:
        """Clear conversation messages.

        Args:
            user_id: User's ID.
            session_id: Optional session filter. If None, clears all.
            clear_cron_only: If True, only deletes cron task messages.

        Returns:
            Number of messages deleted.
        """
        from app.models.orm import ConversationMessageModel

        async with self._session_factory() as session:
            try:
                query = delete(ConversationMessageModel).where(
                    ConversationMessageModel.user_id == user_id
                )

                if session_id:
                    query = query.where(ConversationMessageModel.session_id == session_id)

                if clear_cron_only:
                    query = query.where(ConversationMessageModel.is_cron == True)

                result = await session.execute(query)
                await session.commit()
                return result.rowcount
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to clear conversation: %s", e)
                return 0

    async def get_cron_conversation(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """Load cron task messages for audit/debugging.

        Args:
            user_id: User's ID.
            limit: Maximum messages to return (default: 100).

        Returns:
            List of cron message dicts with role, content, and created_at.
        """
        from app.models.orm import ConversationMessageModel

        async with self._session_factory() as session:
            try:
                query = select(ConversationMessageModel).where(
                    ConversationMessageModel.user_id == user_id,
                    ConversationMessageModel.is_cron == True,
                ).order_by(
                    ConversationMessageModel.created_at.desc()
                ).limit(limit)

                result = await session.execute(query)
                messages = result.scalars().all()

                return [
                    {
                        "role": m.role,
                        "content": m.content,
                        "created_at": m.created_at.isoformat(),
                        "session_id": m.session_id,
                    }
                    for m in messages
                ]
            except SQLAlchemyError as e:
                logger.error("Failed to load cron conversation: %s", e)
                return []

    async def count_messages(
        self,
        user_id: str,
        session_id: str | None = None,
        exclude_cron: bool = True,
    ) -> int:
        """Count messages for a user.

        Args:
            user_id: User's ID.
            session_id: Optional session filter.
            exclude_cron: If True, excludes cron task messages.

        Returns:
            Number of messages.
        """
        from app.models.orm import ConversationMessageModel

        async with self._session_factory() as session:
            try:
                query = select(func.count(ConversationMessageModel.id)).where(
                    ConversationMessageModel.user_id == user_id
                )

                if session_id:
                    query = query.where(ConversationMessageModel.session_id == session_id)

                if exclude_cron:
                    query = query.where(ConversationMessageModel.is_cron == False)

                result = await session.execute(query)
                return result.scalar() or 0
            except SQLAlchemyError as e:
                logger.error("Failed to count messages: %s", e)
                return 0
