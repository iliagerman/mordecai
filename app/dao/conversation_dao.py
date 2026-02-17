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

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, func
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _extract_text_preview_from_message(message: dict) -> str:
    """Best-effort extraction of a human-readable text preview from a structured message.

    We keep the legacy `content` column populated for quick browsing/debugging.
    For structured messages that contain no text blocks (e.g., tool-only turns),
    we return a compact placeholder.
    """

    try:
        content = message.get("content")
    except Exception:
        content = None

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block.strip())
                continue
            if isinstance(block, dict):
                # Common Strands/Bedrock content blocks: {"text": "..."}
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    # Fallback: at least store the role so the row is non-empty.
    role = message.get("role") if isinstance(message, dict) else None
    if isinstance(role, str) and role:
        return f"[{role} structured message]"
    return "[structured message]"


def _safe_json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _safe_json_loads(text: str) -> dict | list | str | int | float | bool | None:
    return json.loads(text)


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
                    content_json=None,
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

    async def save_structured_message(
        self,
        *,
        user_id: str,
        session_id: str,
        message: dict,
        is_cron: bool = False,
        created_at: datetime | None = None,
        redact: bool = True,
    ) -> bool:
        """Save a structured conversation message.

        This stores both:
        - `content`: best-effort text preview for legacy consumers
        - `content_json`: JSON-encoded structured payload for exact reconstruction

        Args:
            user_id: User's ID.
            session_id: Session/thread identifier.
            message: Structured message dict (e.g. {role, content:[...]})
            is_cron: Whether this is a cron message.
            created_at: Optional timestamp.
            redact: If True, apply redaction to minimize secret persistence.

        Returns:
            True on success.
        """

        from app.models.orm import ConversationMessageModel

        if not isinstance(message, dict):
            raise TypeError(f"message must be a dict, got {type(message)}")

        role = message.get("role")
        if not isinstance(role, str) or not role:
            raise ValueError("message.role must be a non-empty string")

        # Best-effort redaction for safety.
        payload_obj: object = message
        if redact:
            try:
                from app.observability.redaction import sanitize

                payload_obj = sanitize(message, max_depth=10, max_chars=20_000)
            except Exception:
                payload_obj = message

        content_preview = _extract_text_preview_from_message(message)
        payload_json = _safe_json_dumps(payload_obj)

        async with self._session_factory() as session:
            try:
                msg = ConversationMessageModel(
                    user_id=user_id,
                    session_id=session_id,
                    role=role,
                    content=content_preview,
                    content_json=payload_json,
                    is_cron=is_cron,
                    created_at=created_at or datetime.utcnow(),
                )
                session.add(msg)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save structured conversation message: %s", e)
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

                if limit:
                    # For limit, we want the most recent N messages.  Order
                    # DESC + LIMIT, then reverse to chronological after fetch.
                    query = query.order_by(ConversationMessageModel.created_at.desc())
                    query = query.limit(limit)
                else:
                    query = query.order_by(ConversationMessageModel.created_at.asc())

                result = await session.execute(query)
                messages = result.scalars().all()

                # If we used desc() for limit, reverse back to chronological order
                if limit:
                    messages = list(reversed(messages))

                return [{"role": m.role, "content": m.content} for m in messages]
            except SQLAlchemyError as e:
                logger.error("Failed to load conversation: %s", e)
                return []

    async def get_conversation_structured(
        self,
        user_id: str,
        session_id: str | None = None,
        exclude_cron: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Load conversation history, preferring structured payloads when available.

        Returns message dicts suitable for feeding back into Strands Agent(messages=...).
        Older rows created before the migration will not have `content_json`; those are
        converted to a minimal {role, content:[{"text": ...}]} shape.
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

                if limit:
                    # Most recent N messages: DESC + LIMIT, reversed after fetch.
                    query = query.order_by(ConversationMessageModel.created_at.desc())
                    query = query.limit(limit)
                else:
                    query = query.order_by(ConversationMessageModel.created_at.asc())

                result = await session.execute(query)
                messages = result.scalars().all()

                if limit:
                    messages = list(reversed(messages))

                out: list[dict] = []
                for m in messages:
                    if m.content_json:
                        try:
                            loaded = _safe_json_loads(m.content_json)
                            if isinstance(loaded, dict):
                                out.append(loaded)
                                continue
                        except Exception:
                            # Fall back to minimal form below.
                            pass

                    # Legacy minimal message shape.
                    out.append({"role": m.role, "content": [{"text": m.content}]})

                return out
            except SQLAlchemyError as e:
                logger.error("Failed to load structured conversation: %s", e)
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
                query = (
                    select(ConversationMessageModel)
                    .where(
                        ConversationMessageModel.user_id == user_id,
                        ConversationMessageModel.is_cron == True,
                    )
                    .order_by(ConversationMessageModel.created_at.desc())
                    .limit(limit)
                )

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

    async def get_latest_session_id(
        self,
        user_id: str,
        exclude_cron: bool = True,
    ) -> str | None:
        """Return the session_id of the most recent message for a user.

        Skips job-thread session IDs (those containing ``__job__``) so the
        caller gets the main conversation thread.

        Returns:
            The latest main-thread session_id, or None if no messages exist.
        """
        from app.models.orm import ConversationMessageModel

        async with self._session_factory() as session:
            try:
                query = (
                    select(ConversationMessageModel.session_id)
                    .where(ConversationMessageModel.user_id == user_id)
                    .order_by(ConversationMessageModel.created_at.desc())
                    .limit(50)
                )

                if exclude_cron:
                    query = query.where(ConversationMessageModel.is_cron == False)

                result = await session.execute(query)
                rows = result.scalars().all()

                for sid in rows:
                    if sid and "__job__" not in sid:
                        return sid

                return None
            except SQLAlchemyError as e:
                logger.error("Failed to get latest session_id: %s", e)
                return None

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

    # ========================================================================
    # Multi-Agent Conversation Methods
    # ========================================================================

    async def create_conversation(
        self,
        creator_user_id: str,
        topic: str,
        max_iterations: int = 5,
    ) -> str:
        """Create a new multi-agent conversation.

        Args:
            creator_user_id: ID of the user creating the conversation.
            topic: The topic/question for the conversation.
            max_iterations: Maximum number of iterations before ending.

        Returns:
            The created conversation ID.
        """
        from app.models.orm import ConversationModel
        import uuid

        conversation_id = str(uuid.uuid4())

        async with self._session_factory() as session:
            try:
                conv = ConversationModel(
                    id=conversation_id,
                    creator_user_id=creator_user_id,
                    topic=topic,
                    max_iterations=max_iterations,
                    current_iteration=0,
                    status="active",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(conv)
                await session.commit()
                logger.info("Created conversation %s for user %s", conversation_id, creator_user_id)
                return conversation_id
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to create conversation: %s", e)
                raise

    async def get_conversation_by_id(self, conversation_id: str) -> dict | None:
        """Get a conversation by ID.

        Args:
            conversation_id: The conversation ID.

        Returns:
            Dictionary with conversation data or None.
        """
        from app.models.orm import ConversationModel

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    select(ConversationModel).where(ConversationModel.id == conversation_id)
                )
                conv = result.scalar_one_or_none()
                if not conv:
                    return None

                return {
                    "id": conv.id,
                    "creator_user_id": conv.creator_user_id,
                    "topic": conv.topic,
                    "max_iterations": conv.max_iterations,
                    "current_iteration": conv.current_iteration,
                    "status": conv.status,
                    "exit_reason": conv.exit_reason,
                    "telegram_group_id": conv.telegram_group_id,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                }
            except SQLAlchemyError as e:
                logger.error("Failed to get conversation: %s", e)
                return None

    async def update_conversation_status(
        self,
        conversation_id: str,
        status: str | None = None,
        current_iteration: int | None = None,
        exit_reason: str | None = None,
        telegram_group_id: int | None = None,
    ) -> bool:
        """Update conversation status or iteration count.

        Args:
            conversation_id: The conversation ID.
            status: New status to set.
            current_iteration: New iteration count.
            exit_reason: Reason for conversation ending.
            telegram_group_id: Telegram group chat ID.

        Returns:
            True if successful, False otherwise.
        """
        from app.models.orm import ConversationModel

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    select(ConversationModel).where(ConversationModel.id == conversation_id)
                )
                conv = result.scalar_one_or_none()
                if not conv:
                    return False

                if status is not None:
                    conv.status = status
                if current_iteration is not None:
                    conv.current_iteration = current_iteration
                if exit_reason is not None:
                    conv.exit_reason = exit_reason
                if telegram_group_id is not None:
                    conv.telegram_group_id = telegram_group_id

                conv.updated_at = datetime.utcnow()
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to update conversation: %s", e)
                return False

    async def increment_conversation_iteration(self, conversation_id: str) -> bool:
        """Increment the iteration count for a conversation.

        Args:
            conversation_id: The conversation ID.

        Returns:
            True if successful, False otherwise.
        """
        from app.models.orm import ConversationModel
        from sqlalchemy import update as sql_update

        async with self._session_factory() as session:
            try:
                stmt = (
                    sql_update(ConversationModel)
                    .where(ConversationModel.id == conversation_id)
                    .values(current_iteration=ConversationModel.current_iteration + 1)
                )
                await session.execute(stmt)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to increment iteration: %s", e)
                return False

    async def add_participant(
        self,
        conversation_id: str,
        user_id: str,
        agent_name: str | None = None,
    ) -> bool:
        """Add a participant to a conversation.

        Args:
            conversation_id: The conversation ID.
            user_id: The user ID (agent owner).
            agent_name: Optional custom agent name.

        Returns:
            True if successful, False otherwise.
        """
        from app.models.orm import ConversationParticipantModel

        async with self._session_factory() as session:
            try:
                participant = ConversationParticipantModel(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    agent_name=agent_name,
                    has_agreed=False,
                    joined_at=datetime.utcnow(),
                )
                session.add(participant)
                await session.commit()
                logger.info("Added participant %s to conversation %s", user_id, conversation_id)
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to add participant: %s", e)
                return False

    async def get_participants(self, conversation_id: str) -> list[dict]:
        """Get all participants for a conversation.

        Args:
            conversation_id: The conversation ID.

        Returns:
            List of participant dictionaries.
        """
        from app.models.orm import ConversationParticipantModel

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    select(ConversationParticipantModel)
                    .where(ConversationParticipantModel.conversation_id == conversation_id)
                    .order_by(ConversationParticipantModel.joined_at.asc())
                )
                participants = result.scalars().all()

                return [
                    {
                        "id": p.id,
                        "conversation_id": p.conversation_id,
                        "user_id": p.user_id,
                        "agent_name": p.agent_name,
                        "has_agreed": p.has_agreed,
                        "joined_at": p.joined_at.isoformat() if p.joined_at else None,
                    }
                    for p in participants
                ]
            except SQLAlchemyError as e:
                logger.error("Failed to get participants: %s", e)
                return []

    async def mark_participant_agreed(self, conversation_id: str, user_id: str) -> bool:
        """Mark a participant as having agreed.

        Args:
            conversation_id: The conversation ID.
            user_id: The user ID (agent) to mark as agreed.

        Returns:
            True if successful, False otherwise.
        """
        from app.models.orm import ConversationParticipantModel

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    select(ConversationParticipantModel).where(
                        ConversationParticipantModel.conversation_id == conversation_id,
                        ConversationParticipantModel.user_id == user_id,
                    )
                )
                participant = result.scalar_one_or_none()
                if not participant:
                    return False

                participant.has_agreed = True
                await session.commit()
                logger.info("Marked participant %s as agreed in conversation %s", user_id, conversation_id)
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to mark agreed: %s", e)
                return False

    async def add_conversation_message(
        self,
        conversation_id: str,
        participant_user_id: str,
        content: str,
        iteration_number: int,
        is_private_instruction: bool = False,
    ) -> bool:
        """Add a message to a multi-agent conversation.

        Args:
            conversation_id: The conversation ID.
            participant_user_id: The user ID of who is speaking.
            content: The message content.
            iteration_number: Which iteration this message belongs to.
            is_private_instruction: Whether this is a private instruction from owner.

        Returns:
            True if successful, False otherwise.
        """
        from app.models.orm import MultiAgentConversationMessageModel

        async with self._session_factory() as session:
            try:
                msg = MultiAgentConversationMessageModel(
                    conversation_id=conversation_id,
                    participant_user_id=participant_user_id,
                    content=content,
                    iteration_number=iteration_number,
                    is_private_instruction=is_private_instruction,
                    created_at=datetime.utcnow(),
                )
                session.add(msg)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to add conversation message: %s", e)
                return False

    async def get_conversation_messages(
        self,
        conversation_id: str,
    ) -> list[dict]:
        """Get all messages for a conversation.

        Args:
            conversation_id: The conversation ID.

        Returns:
            List of message dictionaries ordered by creation time.
        """
        from app.models.orm import MultiAgentConversationMessageModel

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    select(MultiAgentConversationMessageModel)
                    .where(MultiAgentConversationMessageModel.conversation_id == conversation_id)
                    .order_by(MultiAgentConversationMessageModel.created_at.asc())
                )
                messages = result.scalars().all()

                return [
                    {
                        "id": m.id,
                        "conversation_id": m.conversation_id,
                        "participant_user_id": m.participant_user_id,
                        "content": m.content,
                        "iteration_number": m.iteration_number,
                        "is_private_instruction": m.is_private_instruction,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in messages
                ]
            except SQLAlchemyError as e:
                logger.error("Failed to get conversation messages: %s", e)
                return []

    async def get_pending_participants(
        self,
        conversation_id: str,
    ) -> list[dict]:
        """Get participants who haven't agreed yet (still need to speak).

        Args:
            conversation_id: The conversation ID.

        Returns:
            List of participant dictionaries ordered by joined time.
        """
        from app.models.orm import ConversationParticipantModel

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    select(ConversationParticipantModel)
                    .where(
                        ConversationParticipantModel.conversation_id == conversation_id,
                        ConversationParticipantModel.has_agreed == False,
                    )
                    .order_by(ConversationParticipantModel.joined_at.asc())
                )
                participants = result.scalars().all()

                return [
                    {
                        "id": p.id,
                        "conversation_id": p.conversation_id,
                        "user_id": p.user_id,
                        "agent_name": p.agent_name,
                        "has_agreed": p.has_agreed,
                        "joined_at": p.joined_at.isoformat() if p.joined_at else None,
                    }
                    for p in participants
                ]
            except SQLAlchemyError as e:
                logger.error("Failed to get pending participants: %s", e)
                return []

    async def check_all_agreed(self, conversation_id: str) -> bool:
        """Check if all participants in a conversation have agreed.

        Args:
            conversation_id: The conversation ID.

        Returns:
            True if all participants have agreed, False otherwise.
        """
        from app.models.orm import ConversationParticipantModel
        from sqlalchemy import func as sql_func

        async with self._session_factory() as session:
            try:
                # Count non-agreed participants
                result = await session.execute(
                    select(sql_func.count(ConversationParticipantModel.id))
                    .where(
                        ConversationParticipantModel.conversation_id == conversation_id,
                        ConversationParticipantModel.has_agreed == False,
                    )
                )
                count = result.scalar()
                return count == 0
            except SQLAlchemyError as e:
                logger.error("Failed to check all agreed: %s", e)
                return False
