"""Cron task data access operations."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.domain import CronTask
from app.models.orm import CronTaskModel


class CronDAO(BaseDAO[CronTask]):
    """Data access object for Cron Task operations.

    All methods return Pydantic CronTask models, never SQLAlchemy objects.
    """

    async def create(
        self,
        user_id: str,
        name: str,
        instructions: str,
        cron_expression: str,
        next_execution_at: datetime,
    ) -> CronTask:
        """Create a new cron task.

        Args:
            user_id: Owner user identifier.
            name: Human-readable name for the task.
            instructions: The message/command to execute.
            cron_expression: Standard 5-field cron expression.
            next_execution_at: Calculated next execution time.

        Returns:
            Created CronTask domain model.
        """
        now = datetime.utcnow()
        task_id = str(uuid4())

        async with self._db.session() as session:
            cron_model = CronTaskModel(
                id=task_id,
                user_id=user_id,
                name=name,
                instructions=instructions,
                cron_expression=cron_expression,
                enabled=True,
                created_at=now,
                updated_at=now,
                last_executed_at=None,
                next_execution_at=next_execution_at,
            )
            session.add(cron_model)
            await session.flush()

            return CronTask(
                id=cron_model.id,
                user_id=cron_model.user_id,
                name=cron_model.name,
                instructions=cron_model.instructions,
                cron_expression=cron_model.cron_expression,
                enabled=cron_model.enabled,
                created_at=cron_model.created_at,
                updated_at=cron_model.updated_at,
                last_executed_at=cron_model.last_executed_at,
                next_execution_at=cron_model.next_execution_at,
            )

    async def get_by_id(self, task_id: str) -> CronTask | None:
        """Get cron task by ID.

        Args:
            task_id: Task identifier.

        Returns:
            CronTask domain model if found, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronTaskModel).where(CronTaskModel.id == task_id)
            )
            cron_model = result.scalar_one_or_none()

            if cron_model is None:
                return None

            return CronTask(
                id=cron_model.id,
                user_id=cron_model.user_id,
                name=cron_model.name,
                instructions=cron_model.instructions,
                cron_expression=cron_model.cron_expression,
                enabled=cron_model.enabled,
                created_at=cron_model.created_at,
                updated_at=cron_model.updated_at,
                last_executed_at=cron_model.last_executed_at,
                next_execution_at=cron_model.next_execution_at,
            )

    async def get_by_user_and_name(
        self,
        user_id: str,
        name: str,
    ) -> CronTask | None:
        """Get cron task by user ID and name.

        Args:
            user_id: User identifier.
            name: Task name.

        Returns:
            CronTask domain model if found, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronTaskModel)
                .where(CronTaskModel.user_id == user_id)
                .where(CronTaskModel.name == name)
            )
            cron_model = result.scalar_one_or_none()

            if cron_model is None:
                return None

            return CronTask(
                id=cron_model.id,
                user_id=cron_model.user_id,
                name=cron_model.name,
                instructions=cron_model.instructions,
                cron_expression=cron_model.cron_expression,
                enabled=cron_model.enabled,
                created_at=cron_model.created_at,
                updated_at=cron_model.updated_at,
                last_executed_at=cron_model.last_executed_at,
                next_execution_at=cron_model.next_execution_at,
            )

    async def list_by_user(self, user_id: str) -> list[CronTask]:
        """List all cron tasks for a user.

        Args:
            user_id: User identifier.

        Returns:
            List of CronTask domain models.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronTaskModel)
                .where(CronTaskModel.user_id == user_id)
                .order_by(CronTaskModel.created_at.desc())
            )
            cron_models = result.scalars().all()

            return [
                CronTask(
                    id=m.id,
                    user_id=m.user_id,
                    name=m.name,
                    instructions=m.instructions,
                    cron_expression=m.cron_expression,
                    enabled=m.enabled,
                    created_at=m.created_at,
                    updated_at=m.updated_at,
                    last_executed_at=m.last_executed_at,
                    next_execution_at=m.next_execution_at,
                )
                for m in cron_models
            ]

    async def get_due_tasks(self, now: datetime) -> list[CronTask]:
        """Get tasks due for execution.

        Returns tasks where next_execution_at <= now and enabled = true.

        Args:
            now: Current datetime to compare against.

        Returns:
            List of CronTask domain models due for execution.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronTaskModel)
                .where(CronTaskModel.next_execution_at <= now)
                .where(CronTaskModel.enabled == True)  # noqa: E712
                .order_by(CronTaskModel.next_execution_at.asc())
            )
            cron_models = result.scalars().all()

            return [
                CronTask(
                    id=m.id,
                    user_id=m.user_id,
                    name=m.name,
                    instructions=m.instructions,
                    cron_expression=m.cron_expression,
                    enabled=m.enabled,
                    created_at=m.created_at,
                    updated_at=m.updated_at,
                    last_executed_at=m.last_executed_at,
                    next_execution_at=m.next_execution_at,
                )
                for m in cron_models
            ]

    async def update_after_execution(
        self,
        task_id: str,
        last_executed_at: datetime,
        next_execution_at: datetime,
    ) -> bool:
        """Update timestamps after task execution.

        Args:
            task_id: Task identifier.
            last_executed_at: Time when task was executed.
            next_execution_at: Calculated next execution time.

        Returns:
            True if task was found and updated, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronTaskModel).where(CronTaskModel.id == task_id)
            )
            cron_model = result.scalar_one_or_none()

            if cron_model is None:
                return False

            cron_model.last_executed_at = last_executed_at
            cron_model.next_execution_at = next_execution_at
            cron_model.updated_at = datetime.utcnow()
            return True

    async def delete(self, task_id: str) -> bool:
        """Delete a cron task by ID.

        Args:
            task_id: Task identifier.

        Returns:
            True if task was found and deleted, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronTaskModel).where(CronTaskModel.id == task_id)
            )
            cron_model = result.scalar_one_or_none()

            if cron_model is None:
                return False

            await session.delete(cron_model)
            return True
