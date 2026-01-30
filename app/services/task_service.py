"""Task business logic service.

This service handles all task-related business logic, orchestrating
DAOs and handling validation. It follows the layered architecture
where services contain business logic and delegate data access to DAOs.
"""

import uuid

from app.dao.log_dao import LogDAO
from app.dao.task_dao import TaskDAO
from app.dao.user_dao import UserDAO
from app.enums import LogSeverity, TaskStatus
from app.models.domain import Task


class TaskService:
    """Task business logic - orchestrates DAOs, handles validation.

    This service provides task management functionality for the kanban
    dashboard, including creating tasks, updating status, and retrieving
    tasks grouped by status.
    """

    def __init__(self, task_dao: TaskDAO, user_dao: UserDAO, log_dao: LogDAO):
        """Initialize TaskService with required DAOs.

        Args:
            task_dao: Data access object for task operations.
            user_dao: Data access object for user operations.
            log_dao: Data access object for logging operations.
        """
        self.task_dao = task_dao
        self.user_dao = user_dao
        self.log_dao = log_dao

    async def create_task(
        self, user_id: str, title: str, description: str = ""
    ) -> Task:
        """Create a new task with validation.

        Args:
            user_id: Owner user identifier.
            title: Task title (cannot be empty).
            description: Optional task description.

        Returns:
            Created Task domain model.

        Raises:
            ValueError: If user not found or title is empty.
        """
        # Validate user exists
        user = await self.user_dao.get_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        # Validate title
        if not title or not title.strip():
            raise ValueError("Task title cannot be empty")

        # Create task
        task_id = str(uuid.uuid4())
        task = await self.task_dao.create(
            task_id, user_id, title.strip(), description
        )

        # Log action
        await self.log_dao.create(
            user_id=user_id,
            action=f"Created task: {title}",
            severity=LogSeverity.INFO,
            details={"task_id": task_id},
        )

        return task

    async def get_tasks_by_user(self, user_id: str) -> list[Task]:
        """Get all tasks for a user.

        Args:
            user_id: User identifier.

        Returns:
            List of Task domain models for the user.
        """
        return await self.task_dao.get_by_user(user_id)

    async def get_tasks_grouped_by_status(
        self, user_id: str
    ) -> dict[str, list[Task]]:
        """Get tasks grouped by status for kanban view.

        Args:
            user_id: User identifier.

        Returns:
            Dictionary with keys 'pending', 'in_progress', 'done',
            each containing a list of tasks with that status.
        """
        tasks = await self.task_dao.get_by_user(user_id)
        return {
            "pending": [t for t in tasks if t.status == TaskStatus.PENDING],
            "in_progress": [
                t for t in tasks if t.status == TaskStatus.IN_PROGRESS
            ],
            "done": [t for t in tasks if t.status == TaskStatus.DONE],
        }

    async def update_task_status(
        self, task_id: str, status: TaskStatus, user_id: str
    ) -> bool:
        """Update task status with validation.

        Args:
            task_id: Task identifier.
            status: New task status.
            user_id: User requesting the update (for authorization).

        Returns:
            True if task was updated successfully.

        Raises:
            ValueError: If task not found.
            PermissionError: If user doesn't own the task.
        """
        # Verify task exists and belongs to user
        task = await self.task_dao.get_by_id(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.user_id != user_id:
            raise PermissionError("Cannot update another user's task")

        # Update status
        success = await self.task_dao.update_status(task_id, status)

        if success:
            await self.log_dao.create(
                user_id=user_id,
                action=f"Updated task status to {status.value}",
                severity=LogSeverity.INFO,
                details={"task_id": task_id, "new_status": status.value},
            )

        return success

    async def start_task(self, task_id: str, user_id: str) -> bool:
        """Move task to in-progress.

        Args:
            task_id: Task identifier.
            user_id: User requesting the update.

        Returns:
            True if task was updated successfully.

        Raises:
            ValueError: If task not found.
            PermissionError: If user doesn't own the task.
        """
        return await self.update_task_status(
            task_id, TaskStatus.IN_PROGRESS, user_id
        )

    async def complete_task(self, task_id: str, user_id: str) -> bool:
        """Move task to done.

        Args:
            task_id: Task identifier.
            user_id: User requesting the update.

        Returns:
            True if task was updated successfully.

        Raises:
            ValueError: If task not found.
            PermissionError: If user doesn't own the task.
        """
        return await self.update_task_status(task_id, TaskStatus.DONE, user_id)
