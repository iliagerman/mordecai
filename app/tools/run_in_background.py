"""Tool for running commands in the background for long-running operations.

This tool allows the agent to spawn a shell command in the background,
return immediately to the user with an acknowledgment, and notify the user
when the task completes with the result.

Concurrency note:
The agent can process multiple messages concurrently. Tool state must be
*per message/task*. We use :mod:`contextvars` so callbacks are isolated to
the current asyncio task (and propagate into the background thread used for
agent invocation via :func:`asyncio.to_thread`).
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args: Any, **_kwargs: Any):
        def decorator(func: Any) -> Any:
            return func

        return decorator


if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SpawnCallback(Protocol):
    """Protocol for the background task spawn callback."""

    def __call__(
        self,
        task_id: str,
        command: str,
        description: str,
        work_dir: str | None,
    ) -> bool:
        """Spawn a background task.

        Args:
            task_id: Unique identifier for the task.
            command: Shell command to execute.
            description: Human-readable description of the task.
            work_dir: Optional working directory.

        Returns:
            True if the task was successfully spawned.
        """
        ...


# Per-task callback reference - set by message processor before agent runs
_spawn_callback: ContextVar[SpawnCallback | None] = ContextVar(
    "background_task_spawn_callback", default=None
)

# Per-task user info for limit checks
_current_user_id: ContextVar[str | None] = ContextVar(
    "background_task_user_id", default=None
)


def set_background_task_context(
    spawn_callback: SpawnCallback,
    user_id: str,
) -> None:
    """Set the context for background task spawning.

    Called by message processor before creating the agent.

    Args:
        spawn_callback: Callback to spawn background tasks.
        user_id: Current user's ID.
    """
    _spawn_callback.set(spawn_callback)
    _current_user_id.set(user_id)


def clear_background_task_context() -> None:
    """Clear the background task context after processing."""
    _spawn_callback.set(None)
    _current_user_id.set(None)


@tool(
    name="run_in_background",
    description=(
        "Run a shell command in the background for long-running operations. "
        "Use this for tasks that may take more than a minute, such as research tools, "
        "large file processing, or complex data analysis. "
        "The command will execute asynchronously and you will be notified when it completes. "
        "You MUST still provide an immediate response to the user explaining that the task "
        "has been started in the background."
    ),
)
def run_in_background(
    command: str,
    description: str,
    work_dir: str | None = None,
) -> str:
    """Run a shell command in the background.

    Args:
        command: The shell command to execute. Must be a complete, valid shell command.
        description: A short human-readable description of what this task does
            (e.g., "Deep research on quantum computing", "Processing 500MB CSV file").
            This will be shown in notifications.
        work_dir: Optional working directory for the command. If not provided,
            uses the user's default working directory.

    Returns:
        Confirmation message with task ID, or an error message.
    """
    command = command.strip()
    description = description.strip()

    if not command:
        return "Error: No command provided. Please specify a shell command to run."

    if not description:
        return "Error: No description provided. Please describe what this task does."

    # Check if callback is set
    callback = _spawn_callback.get()
    if callback is None:
        return (
            "Error: Background task execution is not available in this context. "
            "Please run the command directly using the shell tool instead."
        )

    # Generate task ID
    task_id = uuid.uuid4().hex[:8]

    # Spawn the background task
    try:
        success = callback(task_id, command, description, work_dir)
        if not success:
            return (
                "Error: Could not start background task. "
                "You may have reached the maximum number of concurrent background tasks. "
                "Please wait for existing tasks to complete or run the command directly."
            )
    except Exception as e:
        logger.exception("Failed to spawn background task: %s", e)
        return f"Error: Failed to start background task: {e}"

    logger.info(
        "Spawned background task %s: %s",
        task_id,
        description[:50] + "..." if len(description) > 50 else description,
    )

    return (
        f"Background task started successfully.\n"
        f"Task ID: {task_id}\n"
        f"Description: {description}\n\n"
        f"The user will be notified when the task completes. "
        f"Now provide a response to the user explaining that the task is running in the background."
    )
