"""Background scheduler for cron task execution.

This module provides the CronScheduler that runs every 5 minutes to check
for due tasks and execute them through the agent with proper locking.

Requirements:
- 6.1: Run every 5 minutes to check for due tasks
- 6.2: Attempt to acquire a lock before execution
- 6.3: Execute task instructions via the Agent if lock acquired
- 6.4: Update last_executed_at and calculate next_execution_at
- 6.5: Log errors and release lock on failure
- 6.6: Send execution result to user via Telegram
- 10.5: Schedule hourly file cleanup job
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable
from uuid import uuid4

from croniter import croniter

from app.dao.cron_lock_dao import CronLockDAO
from app.dao.user_dao import UserDAO
from app.enums import LogSeverity
from app.models.domain import CronTask

if TYPE_CHECKING:
    from app.services.agent_service import AgentService
    from app.services.cron_service import CronService
    from app.services.logging_service import LoggingService
    from app.telegram.bot import TelegramBotInterface

logger = logging.getLogger(__name__)


@dataclass
class SystemTask:
    """A system-level scheduled task.

    Attributes:
        name: Human-readable name for the task.
        cron_expression: Standard 5-field cron expression.
        callback: Async function to execute.
        next_execution: Next scheduled execution time.
    """

    name: str
    cron_expression: str
    callback: Callable[[], Awaitable[None]]
    next_execution: datetime = field(default_factory=datetime.utcnow)


class CronScheduler:
    """Background scheduler for cron task execution.

    Runs every 5 minutes to check for due tasks and execute them
    through the agent with proper distributed locking.

    Requirements:
        - 6.1: Run every 5 minutes to check for due tasks
        - 6.2: Attempt to acquire a lock before execution
        - 6.3: Execute task instructions via the Agent if lock acquired
    """

    CHECK_INTERVAL_SECONDS = 120  # 2 minutes

    def __init__(
        self,
        cron_service: "CronService",
        lock_dao: CronLockDAO,
        agent_service: "AgentService",
        user_dao: UserDAO | None = None,
        telegram_bot: "TelegramBotInterface | None" = None,
        logging_service: "LoggingService | None" = None,
        instance_id: str | None = None,
    ) -> None:
        """Initialize the cron scheduler.

        Args:
            cron_service: Service for cron task operations.
            lock_dao: DAO for distributed lock operations.
            agent_service: Agent service for executing task instructions.
            user_dao: DAO for looking up user telegram_id.
            telegram_bot: Telegram bot for sending execution results.
            logging_service: Logging service for activity logs.
            instance_id: Unique identifier for this scheduler instance.
                        If not provided, a UUID will be generated.
        """
        self.cron_service = cron_service
        self.lock_dao = lock_dao
        self.agent_service = agent_service
        self.user_dao = user_dao
        self.telegram_bot = telegram_bot
        self.logging_service = logging_service
        self.instance_id = instance_id or str(uuid4())

        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        # System-level scheduled tasks
        self._system_tasks: list[SystemTask] = []

        logger.info(
            "CronScheduler initialized with instance_id: %s",
            self.instance_id,
        )

    def register_system_task(
        self,
        name: str,
        cron_expression: str,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """Register a system-level scheduled task.

        System tasks run on a cron schedule but are not stored in the
        database. They are used for internal maintenance operations.

        Args:
            name: Human-readable name for the task.
            cron_expression: Standard 5-field cron expression.
            callback: Async function to execute when task is due.

        Requirements:
            - 10.5: Schedule hourly file cleanup job
        """
        # Calculate initial next execution time
        cron = croniter(cron_expression, datetime.utcnow())
        next_execution = cron.get_next(datetime)

        task = SystemTask(
            name=name,
            cron_expression=cron_expression,
            callback=callback,
            next_execution=next_execution,
        )
        self._system_tasks.append(task)

        logger.info(
            "Registered system task '%s' with cron '%s', next: %s",
            name,
            cron_expression,
            next_execution,
        )

    async def start(self) -> None:
        """Start the scheduler background task.

        Begins the background loop that checks for due tasks every 5 minutes.

        Requirements:
            - 6.1: Run every 5 minutes to check for due tasks
        """
        if self._running:
            logger.warning("CronScheduler is already running")
            return

        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_check_loop())

        logger.info(
            "CronScheduler started, checking every %d seconds",
            self.CHECK_INTERVAL_SECONDS,
        )

    async def stop(self) -> None:
        """Stop the scheduler gracefully.

        Signals the background loop to stop and waits for it to complete.
        """
        if not self._running:
            logger.warning("CronScheduler is not running")
            return

        logger.info("Stopping CronScheduler...")
        self._running = False
        self._stop_event.set()

        if self._task:
            try:
                # Wait for the task to complete with a timeout
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "CronScheduler task did not stop gracefully, cancelling"
                )
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

        logger.info("CronScheduler stopped")

    async def _run_check_loop(self) -> None:
        """Main loop that checks for due tasks every 5 minutes.

        Runs continuously until stop() is called, checking for and
        processing due tasks at each interval.

        Requirements:
            - 6.1: Run every 5 minutes to check for due tasks
        """
        logger.info("CronScheduler check loop started")

        while self._running:
            try:
                await self._process_due_tasks()
                await self._process_system_tasks()
            except Exception as e:
                logger.exception("Error in cron scheduler loop: %s", e)

            # Wait for the interval or until stop is signaled
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.CHECK_INTERVAL_SECONDS,
                )
                # If we get here, stop was signaled
                break
            except asyncio.TimeoutError:
                # Normal timeout, continue the loop
                continue

        logger.info("CronScheduler check loop ended")

    async def _process_due_tasks(self) -> None:
        """Get due tasks and execute them with locking.

        Queries for tasks that are due for execution and processes
        each one with proper distributed locking.

        Requirements:
            - 6.2: Attempt to acquire a lock before execution
            - 6.3: Execute task instructions via the Agent if lock acquired
        """
        logger.info("Cron scheduler waking up - checking for due tasks...")

        try:
            due_tasks = await self.cron_service.get_due_tasks()

            if not due_tasks:
                logger.info("No due cron tasks found")
                return

            logger.info("Found %d due cron task(s)", len(due_tasks))

            for task in due_tasks:
                await self._execute_task_with_lock(task)

        except Exception as e:
            logger.exception("Error processing due tasks: %s", e)

    async def _process_system_tasks(self) -> None:
        """Process system-level scheduled tasks.

        Checks each registered system task and executes it if due.
        Updates the next execution time after successful execution.

        Requirements:
            - 10.5: Schedule hourly file cleanup job
        """
        now = datetime.utcnow()

        for task in self._system_tasks:
            if task.next_execution <= now:
                logger.info("Executing system task '%s'", task.name)

                try:
                    await task.callback()

                    # Calculate next execution time
                    cron = croniter(task.cron_expression, now)
                    task.next_execution = cron.get_next(datetime)

                    logger.info(
                        "System task '%s' completed, next: %s",
                        task.name,
                        task.next_execution,
                    )

                except Exception as e:
                    logger.error(
                        "System task '%s' failed: %s",
                        task.name,
                        str(e),
                    )
                    # Still update next execution to avoid repeated failures
                    cron = croniter(task.cron_expression, now)
                    task.next_execution = cron.get_next(datetime)

    async def _execute_task_with_lock(self, task: CronTask) -> None:
        """Execute a single task with distributed locking.

        Attempts to acquire a lock for the task, executes it if successful,
        and handles result notification and error logging.

        Args:
            task: The CronTask to execute.

        Requirements:
            - 6.2: Attempt to acquire a lock before execution
            - 6.3: Execute task instructions via the Agent if lock acquired
            - 6.4: Update last_executed_at and calculate next_execution_at
            - 6.5: Log errors and release lock on failure
            - 6.6: Send execution result to user via Telegram
        """
        # Try to acquire lock
        lock_acquired = await self.lock_dao.try_acquire_lock(
            task.id, self.instance_id
        )

        if not lock_acquired:
            logger.debug(
                "Could not acquire lock for task '%s' (id=%s), skipping",
                task.name,
                task.id,
            )
            return

        logger.info(
            "Executing cron task '%s' (id=%s) for user %s",
            task.name,
            task.id,
            task.user_id,
        )

        try:
            # Execute through agent
            result = await self.agent_service.process_message(
                user_id=task.user_id,
                message=task.instructions,
            )

            # Update timestamps
            now = datetime.utcnow()
            next_execution = self.cron_service.calculate_next_execution(
                task.cron_expression, now
            )
            await self.cron_service.cron_dao.update_after_execution(
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
            await self._send_result_notification(task, result)

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

        except Exception as e:
            logger.error(
                "Failed to execute cron task '%s' (id=%s): %s",
                task.name,
                task.id,
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

            # Send error notification to user
            await self._send_error_notification(task, str(e))

        finally:
            # Always release the lock
            await self.lock_dao.release_lock(task.id)
            logger.debug("Released lock for task '%s'", task.name)

    async def _send_result_notification(
        self,
        task: CronTask,
        result: str,
    ) -> None:
        """Send execution result to user via Telegram.

        Args:
            task: The executed CronTask.
            result: The agent's response text.

        Requirements:
            - 6.6: Send execution result to user via Telegram
        """
        if not self.telegram_bot:
            logger.debug(
                "No Telegram bot configured, skipping result notification"
            )
            return

        try:
            # Look up the user's telegram_id from the database
            chat_id: int | None = None
            if self.user_dao:
                user = await self.user_dao.get_by_id(task.user_id)
                if user and user.telegram_id:
                    try:
                        chat_id = int(user.telegram_id)
                    except ValueError:
                        pass

            if chat_id:
                notification = (
                    f"⏰ Scheduled task '{task.name}' executed:\n\n{result}"
                )
                await self.telegram_bot.send_response(chat_id, notification)
                logger.debug(
                    "Sent result notification for task '%s' to chat %s",
                    task.name,
                    chat_id,
                )
            else:
                logger.warning(
                    "Could not determine chat_id for user '%s', "
                    "skipping notification",
                    task.user_id,
                )

        except Exception as e:
            logger.error(
                "Failed to send result notification for task '%s': %s",
                task.name,
                str(e),
            )

    async def _send_error_notification(
        self,
        task: CronTask,
        error: str,
    ) -> None:
        """Send error notification to user via Telegram.

        Args:
            task: The failed CronTask.
            error: The error message.

        Requirements:
            - 6.5: Log errors and release lock on failure
            - 6.6: Send execution result to user via Telegram
        """
        if not self.telegram_bot:
            logger.debug(
                "No Telegram bot configured, skipping error notification"
            )
            return

        try:
            # Look up the user's telegram_id from the database
            chat_id: int | None = None
            if self.user_dao:
                user = await self.user_dao.get_by_id(task.user_id)
                if user and user.telegram_id:
                    try:
                        chat_id = int(user.telegram_id)
                    except ValueError:
                        pass

            if chat_id:
                notification = (
                    f"❌ Scheduled task '{task.name}' failed:\n\n{error}"
                )
                await self.telegram_bot.send_response(chat_id, notification)
                logger.debug(
                    "Sent error notification for task '%s' to chat %s",
                    task.name,
                    chat_id,
                )

        except Exception as e:
            logger.error(
                "Failed to send error notification for task '%s': %s",
                task.name,
                str(e),
            )

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is currently running.

        Returns:
            True if the scheduler is running, False otherwise.
        """
        return self._running
