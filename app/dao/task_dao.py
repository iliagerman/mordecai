"""Task data access operations."""

from datetime import datetime

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.enums import TaskStatus
from app.models.domain import Task
from app.models.orm import TaskModel


class TaskDAO(BaseDAO[Task]):
    """Data access object for Task operations.

    All methods return Pydantic Task models, never SQLAlchemy objects.
    """

    async def create(
        self,
        task_id: str,
        user_id: str,
        title: str,
        description: str = "",
    ) -> Task:
        """Create a new task.

        Args:
            task_id: Unique task identifier.
            user_id: Owner user identifier.
            title: Task title.
            description: Optional task description.

        Returns:
            Created Task domain model.
        """
        now = datetime.utcnow()
        async with self._db.session() as session:
            task_model = TaskModel(
                id=task_id,
                user_id=user_id,
                title=title,
                description=description,
                status=TaskStatus.PENDING.value,
                created_at=now,
                updated_at=now,
            )
            session.add(task_model)
            await session.flush()

            return Task(
                id=task_model.id,
                user_id=task_model.user_id,
                title=task_model.title,
                description=task_model.description,
                status=TaskStatus(task_model.status),
                created_at=task_model.created_at,
                updated_at=task_model.updated_at,
            )

    async def get_by_id(self, task_id: str) -> Task | None:
        """Get task by ID.

        Args:
            task_id: Task identifier.

        Returns:
            Task domain model if found, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == task_id)
            )
            task_model = result.scalar_one_or_none()

            if task_model is None:
                return None

            return Task(
                id=task_model.id,
                user_id=task_model.user_id,
                title=task_model.title,
                description=task_model.description,
                status=TaskStatus(task_model.status),
                created_at=task_model.created_at,
                updated_at=task_model.updated_at,
            )

    async def get_by_user(self, user_id: str) -> list[Task]:
        """Get all tasks for a user.

        Args:
            user_id: User identifier.

        Returns:
            List of Task domain models.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(TaskModel)
                .where(TaskModel.user_id == user_id)
                .order_by(TaskModel.created_at.desc())
            )
            task_models = result.scalars().all()

            return [
                Task(
                    id=t.id,
                    user_id=t.user_id,
                    title=t.title,
                    description=t.description,
                    status=TaskStatus(t.status),
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
                for t in task_models
            ]

    async def get_by_user_and_status(
        self, user_id: str, status: TaskStatus
    ) -> list[Task]:
        """Get tasks for a user filtered by status.

        Args:
            user_id: User identifier.
            status: Task status to filter by.

        Returns:
            List of Task domain models matching the status.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(TaskModel)
                .where(TaskModel.user_id == user_id)
                .where(TaskModel.status == status.value)
                .order_by(TaskModel.created_at.desc())
            )
            task_models = result.scalars().all()

            return [
                Task(
                    id=t.id,
                    user_id=t.user_id,
                    title=t.title,
                    description=t.description,
                    status=TaskStatus(t.status),
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
                for t in task_models
            ]

    async def update_status(self, task_id: str, status: TaskStatus) -> bool:
        """Update task status.

        Args:
            task_id: Task identifier.
            status: New task status.

        Returns:
            True if task was found and updated, False otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == task_id)
            )
            task_model = result.scalar_one_or_none()

            if task_model is None:
                return False

            task_model.status = status.value
            task_model.updated_at = datetime.utcnow()
            return True
