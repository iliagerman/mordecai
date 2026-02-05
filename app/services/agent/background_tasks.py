"""Background task lifecycle manager for long-running skill execution.

This module provides the BackgroundTaskManager that spawns and tracks
background subprocess execution, invokes the agent with results when
tasks complete, and sends notifications to users via Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import refresh_runtime_env_from_secrets, resolve_user_skills_dir

if TYPE_CHECKING:
    from app.services.agent_service import AgentService
    from app.services.logging_service import LoggingService

logger = logging.getLogger(__name__)


@dataclass
class BackgroundTaskInfo:
    """Information about a running background task."""

    task_id: str
    user_id: str
    chat_id: int
    description: str
    command: str
    work_dir: str | None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    asyncio_task: asyncio.Task[None] | None = None
    process: asyncio.subprocess.Process | None = None


class BackgroundTaskManager:
    """Manages background task execution for long-running skills.

    Spawns subprocesses for background commands, captures output,
    invokes the agent with results, and sends notifications to users.
    """

    def __init__(
        self,
        agent_service: AgentService | None = None,
        response_callback: Callable[[int, str], Awaitable[Any]] | None = None,
        logging_service: LoggingService | None = None,
        max_per_user: int = 3,
        secrets_path: str | Path = "secrets.yml",
        config: Any | None = None,
    ) -> None:
        """Initialize the background task manager.

        Args:
            agent_service: Agent service for processing results.
            response_callback: Async callback to send responses to Telegram.
                Signature: (chat_id, response) -> Any
            logging_service: Optional logging service.
            max_per_user: Maximum concurrent background tasks per user.
            secrets_path: Path to secrets.yml for env var loading.
            config: Application config for skill directory resolution.
        """
        self._agent_service = agent_service
        self._response_callback = response_callback
        self._logging_service = logging_service
        self._max_per_user = max_per_user
        self._secrets_path = Path(secrets_path)
        self._config = config

        # Active tasks: user_id -> {task_id -> BackgroundTaskInfo}
        self._active: dict[str, dict[str, BackgroundTaskInfo]] = {}

    def set_agent_service(self, agent_service: AgentService) -> None:
        """Set the agent service (for late binding during bootstrap)."""
        self._agent_service = agent_service

    def set_response_callback(
        self,
        callback: Callable[[int, str], Awaitable[Any]],
    ) -> None:
        """Set the response callback (for late binding during bootstrap)."""
        self._response_callback = callback

    def count_active(self, user_id: str) -> int:
        """Count active background tasks for a user."""
        return len(self._active.get(user_id, {}))

    def list_active(self, user_id: str) -> list[dict[str, Any]]:
        """List active background tasks for a user.

        Returns:
            List of dicts with task_id, description, started_at.
        """
        tasks = self._active.get(user_id, {})
        return [
            {
                "task_id": info.task_id,
                "description": info.description,
                "started_at": info.started_at.isoformat(),
            }
            for info in tasks.values()
        ]

    def spawn(
        self,
        user_id: str,
        chat_id: int,
        task_id: str,
        command: str,
        description: str,
        work_dir: str | None,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> bool:
        """Spawn a background task.

        This method is thread-safe: it can be called from a background thread
        (e.g., agent running in asyncio.to_thread). When called from a thread,
        pass the event loop explicitly.

        Args:
            user_id: User's ID.
            chat_id: Telegram chat ID for notifications.
            task_id: Unique task identifier.
            command: Shell command to execute.
            description: Human-readable description.
            work_dir: Optional working directory.
            loop: Event loop to schedule the task on (for thread-safety).

        Returns:
            True if the task was spawned, False if limit reached.
        """
        # Check limit
        if self.count_active(user_id) >= self._max_per_user:
            logger.warning(
                "User %s has reached max background tasks (%d)",
                user_id,
                self._max_per_user,
            )
            return False

        # Create task info
        info = BackgroundTaskInfo(
            task_id=task_id,
            user_id=user_id,
            chat_id=chat_id,
            description=description,
            command=command,
            work_dir=work_dir,
        )

        # Register in active tasks
        if user_id not in self._active:
            self._active[user_id] = {}
        self._active[user_id][task_id] = info

        # Create asyncio task - handle thread-safety
        def create_and_register_task() -> None:
            asyncio_task = asyncio.create_task(self._execute(info))
            info.asyncio_task = asyncio_task
            asyncio_task.add_done_callback(
                lambda t: self._cleanup_task(user_id, task_id)
            )

        if loop is not None:
            # Called from background thread - schedule on event loop
            loop.call_soon_threadsafe(create_and_register_task)
        else:
            # Called from within event loop context
            create_and_register_task()

        logger.info(
            "Spawned background task %s for user %s: %s",
            task_id,
            user_id,
            description,
        )
        return True

    def _cleanup_task(self, user_id: str, task_id: str) -> None:
        """Remove a completed task from tracking."""
        if user_id in self._active:
            self._active[user_id].pop(task_id, None)
            if not self._active[user_id]:
                del self._active[user_id]

    async def _execute(self, info: BackgroundTaskInfo) -> None:
        """Execute a background task and process results.

        Runs the command, captures output, invokes the agent with results,
        and sends the response to the user.
        """
        stdout_data = ""
        stderr_data = ""
        exit_code: int | None = None

        try:
            # Prepare environment with skill secrets
            env = os.environ.copy()

            # Load skill env vars from secrets.yml
            try:
                refresh_runtime_env_from_secrets(
                    self._secrets_path,
                    info.user_id,
                    target=env,
                )
            except Exception as e:
                logger.debug("Could not load skill secrets: %s", e)

            # Resolve skills directory for MORDECAI_SKILLS_BASE_DIR
            if self._config is not None:
                try:
                    skills_dir = resolve_user_skills_dir(self._config, info.user_id)
                    env["MORDECAI_SKILLS_BASE_DIR"] = str(skills_dir.parent)
                except Exception:
                    pass

            # Determine working directory
            cwd: str | None = None
            if info.work_dir:
                cwd = info.work_dir
            elif self._agent_service is not None:
                try:
                    wd = self._agent_service._get_user_working_dir(info.user_id)
                    cwd = str(wd)
                except Exception:
                    pass

            logger.info(
                "Executing background task %s: %s (cwd=%s)",
                info.task_id,
                info.command[:100] + "..." if len(info.command) > 100 else info.command,
                cwd,
            )

            # Run subprocess
            process = await asyncio.create_subprocess_shell(
                info.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            info.process = process

            # Wait for completion
            stdout_bytes, stderr_bytes = await process.communicate()
            exit_code = process.returncode

            stdout_data = stdout_bytes.decode("utf-8", errors="replace")
            stderr_data = stderr_bytes.decode("utf-8", errors="replace")

            logger.info(
                "Background task %s completed with exit code %s (stdout=%d bytes, stderr=%d bytes)",
                info.task_id,
                exit_code,
                len(stdout_data),
                len(stderr_data),
            )

        except asyncio.CancelledError:
            logger.info("Background task %s was cancelled", info.task_id)
            await self._send_notification(
                info.chat_id,
                f"⚠️ Background task cancelled: {info.description}",
            )
            return

        except Exception as e:
            logger.exception("Background task %s failed: %s", info.task_id, e)
            await self._send_notification(
                info.chat_id,
                f"❌ Background task failed: {info.description}\n\nError: {e}",
            )
            return

        # Process result through agent
        await self._process_result(info, exit_code, stdout_data, stderr_data)

    async def _process_result(
        self,
        info: BackgroundTaskInfo,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        """Process the task result through the agent and send to user."""
        # Truncate output if very long
        max_output_chars = 50000
        if len(stdout) > max_output_chars:
            stdout = stdout[:max_output_chars] + "\n\n... (output truncated)"
        if len(stderr) > max_output_chars:
            stderr = stderr[:max_output_chars] + "\n\n... (stderr truncated)"

        # Build synthetic message for agent
        status = "completed successfully" if exit_code == 0 else f"failed (exit code {exit_code})"
        synthetic_message = (
            f"[Background Task Result]\n\n"
            f"Task: {info.description}\n"
            f"Status: {status}\n"
            f"Task ID: {info.task_id}\n\n"
        )

        if stdout.strip():
            synthetic_message += f"Output:\n```\n{stdout}\n```\n\n"

        if stderr.strip() and exit_code != 0:
            synthetic_message += f"Errors:\n```\n{stderr}\n```\n\n"

        synthetic_message += (
            "Please summarize this result for the user in a clear, concise way. "
            "Highlight the key information and any actionable insights."
        )

        # Invoke agent with result
        if self._agent_service is not None:
            try:
                response = await self._agent_service.process_message(
                    user_id=info.user_id,
                    message=synthetic_message,
                )

                # Prepend task notification header
                notification = (
                    f"✅ Background task completed: {info.description}\n\n"
                    f"{response}"
                )

                await self._send_notification(info.chat_id, notification)

            except Exception as e:
                logger.exception(
                    "Failed to process background task result through agent: %s", e
                )
                # Fall back to raw output
                await self._send_raw_result(info, exit_code, stdout, stderr)
        else:
            # No agent service, send raw result
            await self._send_raw_result(info, exit_code, stdout, stderr)

    async def _send_raw_result(
        self,
        info: BackgroundTaskInfo,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        """Send raw task result without agent processing."""
        status = "completed" if exit_code == 0 else f"failed (exit code {exit_code})"

        # Truncate for Telegram message limits
        max_len = 3500
        output_preview = stdout[:max_len] if stdout else "(no output)"
        if len(stdout) > max_len:
            output_preview += "\n\n... (truncated)"

        notification = (
            f"{'✅' if exit_code == 0 else '❌'} Background task {status}: "
            f"{info.description}\n\n"
            f"Output:\n{output_preview}"
        )

        if stderr.strip() and exit_code != 0:
            error_preview = stderr[:1000]
            if len(stderr) > 1000:
                error_preview += "\n... (truncated)"
            notification += f"\n\nErrors:\n{error_preview}"

        await self._send_notification(info.chat_id, notification)

    async def _send_notification(self, chat_id: int, message: str) -> None:
        """Send a notification to the user via Telegram."""
        if self._response_callback is None:
            logger.warning(
                "No response callback set, cannot send notification to chat %s",
                chat_id,
            )
            return

        try:
            await self._response_callback(chat_id, message)
        except Exception as e:
            logger.error("Failed to send notification to chat %s: %s", chat_id, e)

    async def cancel(self, user_id: str, task_id: str) -> bool:
        """Cancel a background task.

        Args:
            user_id: User's ID.
            task_id: Task ID to cancel.

        Returns:
            True if the task was found and cancelled.
        """
        tasks = self._active.get(user_id, {})
        info = tasks.get(task_id)
        if info is None:
            return False

        # Kill subprocess if running
        if info.process is not None:
            try:
                info.process.kill()
            except Exception:
                pass

        # Cancel asyncio task
        if info.asyncio_task is not None:
            info.asyncio_task.cancel()

        logger.info("Cancelled background task %s for user %s", task_id, user_id)
        return True

    async def cancel_all(self) -> int:
        """Cancel all active background tasks (for graceful shutdown).

        Returns:
            Number of tasks cancelled.
        """
        count = 0
        for user_id in list(self._active.keys()):
            for task_id in list(self._active.get(user_id, {}).keys()):
                if await self.cancel(user_id, task_id):
                    count += 1

        if count > 0:
            logger.info("Cancelled %d background tasks during shutdown", count)

        return count
