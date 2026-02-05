"""Memory data access operations."""

from datetime import datetime

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.domain import LongMemory
from app.models.orm import LongMemoryModel


class MemoryDAO(BaseDAO[LongMemory]):
    """Data access object for Long Memory operations.

    All methods return Pydantic LongMemory models, never SQLAlchemy objects.
    """

    async def upsert(self, user_id: str, key: str, value: str) -> LongMemory:
        """Insert or update a memory entry.

        If a memory with the same user_id and key exists, it will be updated.
        Otherwise, a new entry will be created.

        Args:
            user_id: User identifier.
            key: Memory key.
            value: Memory value.

        Returns:
            Created or updated LongMemory domain model.
        """
        now = datetime.utcnow()
        async with self._db.session() as session:
            # Check if memory exists
            result = await session.execute(
                select(LongMemoryModel)
                .where(LongMemoryModel.user_id == user_id)
                .where(LongMemoryModel.key == key)
            )
            memory_model = result.scalar_one_or_none()

            if memory_model is not None:
                # Update existing
                memory_model.value = value
                memory_model.updated_at = now
            else:
                # Create new
                memory_model = LongMemoryModel(
                    user_id=user_id,
                    key=key,
                    value=value,
                    updated_at=now,
                )
                session.add(memory_model)

            await session.flush()

            return LongMemory(
                id=memory_model.id,
                user_id=memory_model.user_id,
                key=memory_model.key,
                value=memory_model.value,
                updated_at=memory_model.updated_at,
            )

    async def get(self, user_id: str, key: str) -> LongMemory | None:
        """Get a specific memory entry.

        Args:
            user_id: User identifier.
            key: Memory key.

        Returns:
            LongMemory domain model if found, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(LongMemoryModel)
                .where(LongMemoryModel.user_id == user_id)
                .where(LongMemoryModel.key == key)
            )
            memory_model = result.scalar_one_or_none()

            if memory_model is None:
                return None

            return LongMemory(
                id=memory_model.id,
                user_id=memory_model.user_id,
                key=memory_model.key,
                value=memory_model.value,
                updated_at=memory_model.updated_at,
            )

    async def get_all_for_user(self, user_id: str) -> list[LongMemory]:
        """Get all memory entries for a user.

        Args:
            user_id: User identifier.

        Returns:
            List of LongMemory domain models.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(LongMemoryModel)
                .where(LongMemoryModel.user_id == user_id)
                .order_by(LongMemoryModel.updated_at.desc())
            )
            memory_models = result.scalars().all()

            return [
                LongMemory(
                    id=m.id,
                    user_id=m.user_id,
                    key=m.key,
                    value=m.value,
                    updated_at=m.updated_at,
                )
                for m in memory_models
            ]

    async def delete(self, user_id: str, key: str) -> bool:
        """Delete a memory entry.

        Args:
            user_id: User identifier.
            key: Memory key.

        Returns:
            True if memory was found and deleted, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(LongMemoryModel)
                .where(LongMemoryModel.user_id == user_id)
                .where(LongMemoryModel.key == key)
            )
            memory_model = result.scalar_one_or_none()

            if memory_model is None:
                return False

            await session.delete(memory_model)
            return True
