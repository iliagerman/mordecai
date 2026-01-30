"""User data access operations."""

from datetime import datetime

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.domain import User
from app.models.orm import UserModel


class UserDAO(BaseDAO[User]):
    """Data access object for User operations.

    All methods return Pydantic User models, never SQLAlchemy objects.
    """

    async def create(
        self,
        user_id: str,
        telegram_id: str,
        agent_name: str | None = None
    ) -> User:
        """Create a new user.

        Args:
            user_id: Unique user identifier.
            telegram_id: Telegram user ID for identification.
            agent_name: Optional custom name for the agent.

        Returns:
            Created User domain model.
        """
        now = datetime.utcnow()
        async with self._db.session() as session:
            user_model = UserModel(
                id=user_id,
                telegram_id=telegram_id,
                agent_name=agent_name,
                created_at=now,
                last_active=now,
            )
            session.add(user_model)
            await session.flush()

            return User(
                id=user_model.id,
                telegram_id=user_model.telegram_id,
                agent_name=user_model.agent_name,
                created_at=user_model.created_at,
                last_active=user_model.last_active,
            )

    async def get_or_create(
        self,
        user_id: str,
        telegram_id: str,
    ) -> User:
        """Get existing user or create a new one.

        Args:
            user_id: Unique user identifier (username or string ID).
            telegram_id: Telegram chat ID for sending messages.

        Returns:
            User domain model (existing or newly created).
        """
        existing = await self.get_by_id(user_id)
        if existing:
            # Update telegram_id if it changed
            if existing.telegram_id != telegram_id:
                await self._update_telegram_id(user_id, telegram_id)
                existing = await self.get_by_id(user_id)
            return existing
        return await self.create(user_id, telegram_id)

    async def _update_telegram_id(
        self,
        user_id: str,
        telegram_id: str,
    ) -> None:
        """Update user's telegram_id.

        Args:
            user_id: User identifier.
            telegram_id: New telegram ID.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user_model = result.scalar_one_or_none()
            if user_model:
                user_model.telegram_id = telegram_id

    async def get_by_id(self, user_id: str) -> User | None:
        """Get user by ID.

        Args:
            user_id: User identifier.

        Returns:
            User domain model if found, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user_model = result.scalar_one_or_none()

            if user_model is None:
                return None

            return User(
                id=user_model.id,
                telegram_id=user_model.telegram_id,
                agent_name=user_model.agent_name,
                created_at=user_model.created_at,
                last_active=user_model.last_active,
            )

    async def get_by_telegram_id(self, telegram_id: str) -> User | None:
        """Get user by Telegram ID.

        Args:
            telegram_id: Telegram user identifier.

        Returns:
            User domain model if found, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.telegram_id == telegram_id)
            )
            user_model = result.scalar_one_or_none()

            if user_model is None:
                return None

            return User(
                id=user_model.id,
                telegram_id=user_model.telegram_id,
                agent_name=user_model.agent_name,
                created_at=user_model.created_at,
                last_active=user_model.last_active,
            )

    async def update_last_active(self, user_id: str) -> bool:
        """Update user's last active timestamp.

        Args:
            user_id: User identifier.

        Returns:
            True if user was found and updated, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user_model = result.scalar_one_or_none()

            if user_model is None:
                return False

            user_model.last_active = datetime.utcnow()
            return True

    async def set_agent_name(self, user_id: str, agent_name: str) -> bool:
        """Set the agent name for a user.

        Args:
            user_id: User identifier.
            agent_name: Name to assign to the agent.

        Returns:
            True if user was found and updated, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user_model = result.scalar_one_or_none()

            if user_model is None:
                return False

            user_model.agent_name = agent_name
            user_model.last_active = datetime.utcnow()
            return True

    async def get_agent_name(self, user_id: str) -> str | None:
        """Get the agent name for a user.

        Args:
            user_id: User identifier.

        Returns:
            Agent name if set, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel.agent_name).where(UserModel.id == user_id)
            )
            return result.scalar_one_or_none()
