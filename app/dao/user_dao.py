"""User data access operations."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.dao.base import BaseDAO
from app.models.domain import User
from app.models.orm import UserModel


class UserDAO(BaseDAO[User]):
    """Data access object for User operations.

    All methods return Pydantic User models, never SQLAlchemy objects.
    """

    async def create(self, user_id: str, telegram_id: str, agent_name: str | None = None) -> User:
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
                onboarding_completed=user_model.onboarding_completed,
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

        # If a user already exists for this telegram_id (e.g., retry/race or
        # alternate id mapping), prefer returning it rather than violating the
        # unique constraint.
        existing_by_tg = await self.get_by_telegram_id(telegram_id)
        if existing_by_tg:
            # If the existing user has a numeric ID (legacy) and the new user_id is a username,
            # migrate the user to use the username as their primary identifier.
            if (
                existing_by_tg.id != user_id
                and self._is_numeric_identifier(existing_by_tg.id)
                and self._is_string_identifier(user_id)
            ):
                await self._migrate_user_id(existing_by_tg.id, user_id)
                return await self.get_by_id(user_id)
            return existing_by_tg

        try:
            return await self.create(user_id, telegram_id)
        except IntegrityError:
            # Another concurrent worker likely inserted the user after our checks.
            # Fall back to fetching the existing row.
            existing = await self.get_by_id(user_id)
            if existing:
                return existing
            existing_by_tg = await self.get_by_telegram_id(telegram_id)
            if existing_by_tg:
                if (
                    existing_by_tg.id != user_id
                    and self._is_numeric_identifier(existing_by_tg.id)
                    and self._is_string_identifier(user_id)
                ):
                    await self._migrate_user_id(existing_by_tg.id, user_id)
                    return await self.get_by_id(user_id)
                return existing_by_tg
            raise

    @staticmethod
    def _is_string_identifier(user_id: str) -> bool:
        """Check if user_id is a string identifier (username) rather than numeric.

        A username is considered a string identifier if it contains letters
        or starts with @ (Telegram usernames may have @ prefix in some contexts).
        """
        return any(c.isalpha() for c in user_id) or user_id.startswith("@")

    @staticmethod
    def _is_numeric_identifier(user_id: str) -> bool:
        """Check if user_id is a legacy numeric identifier.

        Historically, some deployments used the Telegram numeric user/chat id as the
        primary user id. We only want to migrate those numeric ids to a string id
        (e.g., username). UUIDs and other non-numeric ids should NOT be migrated.
        """
        return bool(user_id) and user_id.isdigit()

    async def _migrate_user_id(self, old_id: str, new_id: str) -> None:
        """Migrate a user from old_id (numeric) to new_id (username).

        This is used when a user was initially created with a numeric Telegram ID
        and we now have their username.

        Args:
            old_id: The old numeric user ID.
            new_id: The new username-based ID.
        """
        async with self._db.session() as session:
            result = await session.execute(select(UserModel).where(UserModel.id == old_id))
            user_model = result.scalar_one_or_none()
            if user_model:
                user_model.id = new_id

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
            result = await session.execute(select(UserModel).where(UserModel.id == user_id))
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
            result = await session.execute(select(UserModel).where(UserModel.id == user_id))
            user_model = result.scalar_one_or_none()

            if user_model is None:
                return None

            return User(
                id=user_model.id,
                telegram_id=user_model.telegram_id,
                agent_name=user_model.agent_name,
                onboarding_completed=user_model.onboarding_completed,
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
                onboarding_completed=user_model.onboarding_completed,
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
            result = await session.execute(select(UserModel).where(UserModel.id == user_id))
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
            result = await session.execute(select(UserModel).where(UserModel.id == user_id))
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

    async def is_onboarding_completed(self, user_id: str) -> bool:
        """Check if user has completed onboarding.

        Args:
            user_id: User identifier.

        Returns:
            True if onboarding is completed, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(UserModel.onboarding_completed).where(UserModel.id == user_id)
            )
            value = result.scalar_one_or_none()
            return bool(value) if value is not None else False

    async def set_onboarding_completed(self, user_id: str) -> bool:
        """Mark user as having completed onboarding.

        Args:
            user_id: User identifier.

        Returns:
            True if user was found and updated, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(select(UserModel).where(UserModel.id == user_id))
            user_model = result.scalar_one_or_none()

            if user_model is None:
                return False

            user_model.onboarding_completed = True
            user_model.last_active = datetime.utcnow()
            return True
