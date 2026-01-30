"""Cron task business logic service.

This service handles all cron task-related business logic, including
validation, scheduling, and execution. It delegates data access to
CronDAO and CronLockDAO.

Requirements:
- 5.1: Validate cron expressions before creating tasks
- 5.2: Provide methods to create, list, and delete cron tasks
- 5.3: Provide a method to get and execute due tasks with proper locking
- 5.4: Execute task instructions through the Agent as if user sent them
- 5.5: Return descriptive error message if cron expression validation fails
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from croniter import croniter

from app.dao.cron_dao import CronDAO
from app.dao.cron_lock_dao import CronLockDAO
from app.enums import LogSeverity
from app.models.domain import CronTask

if TYPE_CHECKING:
    from app.services.agent_service import AgentService
    from app.services.logging_service import LoggingService
    from app.telegram.bot import TelegramBotInterface

logger = logging.getLogger(__name__)


class CronExpressionError(Exception):
    """Raised when a cron expression is invalid."""

    pass


class CronTaskNotFoundError(Exception):
    """Raised when a cron task is not found."""

    pass


class CronTaskDuplicateError(Exception):
    """Raised when a cron task with the same name already exists."""

    pass


class CronService:
    """Cron task business logic.

    Handles validation, creation, listing, deletion, and execution
    of cron tasks. All database operations are delegated to DAOs.
    """

    def __init__(
        self,
        cron_dao: CronDAO,
        lock_dao: CronLockDAO,
        agent_service: "AgentService",
        telegram_bot: "TelegramBotInterface | None" = None,
        logging_service: "LoggingService | None" = None,
    ) -> None:
        """Initialize the cron service.

        Args:
            cron_dao: Data access object for cron task operations.
            lock_dao: Data access object for cron lock operations.
            agent_service: Agent service for executing task instructions.
            telegram_bot: Telegram bot for sending execution results.
            logging_service: Logging service for activity logs.
        """
        self.cron_dao = cron_dao
        self.lock_dao = lock_dao
        self.agent_service = agent_service
        self.telegram_bot = telegram_bot
        self.logging_service = logging_service

    def validate_cron_expression(self, expression: str) -> bool:
        """Validate a cron expression is syntactically correct.

        Validates that the expression is a valid 5-field cron expression
        (minute hour day month weekday).

        Args:
            expression: Cron expression string to validate.

        Returns:
            True if the expression is valid.

        Raises:
            CronExpressionError: If the expression is invalid.

        Requirements:
            - 5.1: Validate cron expressions before creating tasks
            - 8.1: Accept standard 5-field cron expressions
            - 8.2: Validate that cron expressions are syntactically correct
        """
        if not expression or not expression.strip():
            raise CronExpressionError(
                "Invalid cron expression: expression cannot be empty"
            )

        expression = expression.strip()
        fields = expression.split()

        if len(fields) != 5:
            raise CronExpressionError(
                f"Invalid cron expression: expected 5 fields "
                f"(minute hour day month weekday), got {len(fields)}"
            )

        try:
            # croniter validates the expression when instantiated
            croniter(expression)
            return True
        except (ValueError, KeyError) as e:
            raise CronExpressionError(
                f"Invalid cron expression: {str(e)}"
            ) from e

    def calculate_next_execution(
        self,
        expression: str,
        from_time: datetime | None = None,
    ) -> datetime:
        """Calculate next execution time from cron expression.

        Args:
            expression: Valid cron expression string.
            from_time: Reference time to calculate from (default: now).

        Returns:
            Next execution datetime.

        Raises:
            CronExpressionError: If the expression is invalid.

        Requirements:
            - 1.3: Calculate and store next_execution_at based on expression
            - 8.3: Calculate the next execution time from a cron expression
        """
        if from_time is None:
            from_time = datetime.utcnow()

        try:
            cron = croniter(expression, from_time)
            return cron.get_next(datetime)
        except (ValueError, KeyError) as e:
            raise CronExpressionError(
                f"Invalid cron expression: {str(e)}"
            ) from e

    async def create_task(
        self,
        user_id: str,
        name: str,
        instructions: str,
        cron_expression: str,
    ) -> CronTask:
        """Create a new cron task with validation.

        Args:
            user_id: Owner user identifier.
            name: Human-readable name for the task.
            instructions: The message/command to execute.
            cron_expression: Standard 5-field cron expression.

        Returns:
            Created CronTask domain model.

        Raises:
            CronExpressionError: If the cron expression is invalid.
            CronTaskDuplicateError: If a task with the same name exists.

        Requirements:
            - 5.1: Validate cron expressions before creating tasks
            - 5.2: Provide methods to create cron tasks
            - 5.5: Return descriptive error message if validation fails
        """
        # Validate the cron expression
        self.validate_cron_expression(cron_expression)

        # Check for duplicate task name
        existing = await self.cron_dao.get_by_user_and_name(user_id, name)
        if existing is not None:
            raise CronTaskDuplicateError(
                f"A task named '{name}' already exists for this user"
            )

        # Calculate next execution time
        next_execution = self.calculate_next_execution(cron_expression)

        # Create the task
        task = await self.cron_dao.create(
            user_id=user_id,
            name=name,
            instructions=instructions,
            cron_expression=cron_expression,
            next_execution_at=next_execution,
        )

        logger.info(
            "Created cron task '%s' for user %s, next execution: %s",
            name,
            user_id,
            next_execution,
        )

        # Log the action
        if self.logging_service:
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Created cron task: {name}",
                severity=LogSeverity.INFO,
                details={
                    "task_id": task.id,
                    "cron_expression": cron_expression,
                    "next_execution": next_execution.isoformat(),
                },
            )

        return task

    async def list_tasks(self, user_id: str) -> list[CronTask]:
        """List all cron tasks for a user.

        Args:
            user_id: User identifier.

        Returns:
            List of CronTask domain models.

        Requirements:
            - 5.2: Provide methods to list cron tasks
        """
        return await self.cron_dao.list_by_user(user_id)

    async def delete_task(
        self,
        user_id: str,
        task_identifier: str,
    ) -> bool:
        """Delete a task by name or ID.

        Args:
            user_id: User identifier.
            task_identifier: Task name or ID to delete.

        Returns:
            True if task was deleted.

        Raises:
            CronTaskNotFoundError: If the task is not found.

        Requirements:
            - 5.2: Provide methods to delete cron tasks
        """
        # First try to find by name
        task = await self.cron_dao.get_by_user_and_name(
            user_id, task_identifier
        )

        # If not found by name, try by ID
        if task is None:
            task = await self.cron_dao.get_by_id(task_identifier)
            # Verify the task belongs to this user
            if task is not None and task.user_id != user_id:
                task = None

        if task is None:
            raise CronTaskNotFoundError(
                f"Task '{task_identifier}' not found"
            )

        # Release any existing lock
        await self.lock_dao.release_lock(task.id)

        # Delete the task
        deleted = await self.cron_dao.delete(task.id)

        if deleted:
            logger.info(
                "Deleted cron task '%s' (id=%s) for user %s",
                task.name,
                task.id,
                user_id,
            )

            # Log the action
            if self.logging_service:
                await self.logging_service.log_action(
                    user_id=user_id,
                    action=f"Deleted cron task: {task.name}",
                    severity=LogSeverity.INFO,
                    details={"task_id": task.id},
                )

        return deleted

    async def get_due_tasks(self) -> list[CronTask]:
        """Get all tasks due for execution.

        Returns tasks where next_execution_at <= now and enabled = true.

        Returns:
            List of CronTask domain models due for execution.

        Requirements:
            - 5.3: Provide a method to get due tasks
        """
        now = datetime.utcnow()
        return await self.cron_dao.get_due_tasks(now)

    async def execute_task(
        self,
        task: CronTask,
        instance_id: str,
        chat_id: int | None = None,
    ) -> str:
        """Execute a task through the agent and return result.

        Acquires a lock, executes the task instructions through the agent,
        updates timestamps, and releases the lock.

        Args:
            task: CronTask to execute.
            instance_id: Unique identifier for this scheduler instance.
            chat_id: Telegram chat ID to send results to (optional).

        Returns:
            Agent's response text.

        Requirements:
            - 5.3: Execute due tasks with proper locking
            - 5.4: Execute task instructions through the Agent
        """
        # Try to acquire lock
        lock_acquired = await self.lock_dao.try_acquire_lock(
            task.id, instance_id
        )

        if not lock_acquired:
            logger.debug(
                "Could not acquire lock for task %s, skipping",
                task.id,
            )
            return ""

        try:
            logger.info(
                "Executing cron task '%s' (id=%s) for user %s",
                task.name,
                task.id,
                task.user_id,
            )

            # Execute through agent
            result = await self.agent_service.process_message(
                user_id=task.user_id,
                message=task.instructions,
            )

            # Update timestamps
            now = datetime.utcnow()
            next_execution = self.calculate_next_execution(
                task.cron_expression, now
            )
            await self.cron_dao.update_after_execution(
                task_id=task.id,
                last_executed_at=now,
                next_execution_at=next_execution,
            )

            logger.info(
                "Cron task '%s' executed successfully, next: %s",
                task.name,
                next_execution,
            )

            # Send result to user via Telegram
            if self.telegram_bot and chat_id:
                notification = (
                    f"⏰ Scheduled task '{task.name}' executed:\n\n{result}"
                )
                await self.telegram_bot.send_response(chat_id, notification)

            # Log the action
            if self.logging_service:
                await self.logging_service.log_action(
                    user_id=task.user_id,
                    action=f"Executed cron task: {task.name}",
                    severity=LogSeverity.INFO,
                    details={
                        "task_id": task.id,
                        "next_execution": next_execution.isoformat(),
                    },
                )

            return result

        except Exception as e:
            logger.error(
                "Failed to execute cron task '%s': %s",
                task.name,
                str(e),
            )

            # Log the error
            if self.logging_service:
                await self.logging_service.log_action(
                    user_id=task.user_id,
                    action=f"Cron task execution failed: {task.name}",
                    severity=LogSeverity.ERROR,
                    details={"task_id": task.id, "error": str(e)},
                )

            raise

        finally:
            # Always release the lock
            await self.lock_dao.release_lock(task.id)
