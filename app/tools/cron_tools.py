"""Tools for managing cron scheduled tasks.

These tools allow the agent to create, list, and delete scheduled tasks
through natural language conversation. The tools integrate with CronService
for business logic and validation.

Requirements:
- 7.1: create_cron_task tool that accepts name, instructions, cron_expression
- 7.2: list_cron_tasks tool that returns all cron tasks for current user
- 7.3: delete_cron_task tool that removes a task by name or id
- 7.5: Validate inputs and return user-friendly error messages
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator

if TYPE_CHECKING:
    from app.services.cron_service import CronService

logger = logging.getLogger(__name__)


# Global references - set by agent_service before creating the agent
_cron_service: "CronService | None" = None
_current_user_id: str | None = None


def set_cron_context(
    cron_service: "CronService",
    user_id: str,
) -> None:
    """Set the cron service and user context for the tools.

    Called by agent_service before creating the agent.

    Args:
        cron_service: CronService instance for task operations.
        user_id: Current user's identifier.
    """
    global _cron_service, _current_user_id
    _cron_service = cron_service
    _current_user_id = user_id


def _run_async(coro):
    """Run an async coroutine from sync context.

    Uses nest_asyncio to allow running async code within an already
    running event loop, avoiding the need for a separate thread/connection
    which causes SQLite "database is locked" errors.
    """
    import nest_asyncio
    nest_asyncio.apply()

    try:
        loop = asyncio.get_running_loop()
        # We're in an async context - run directly in the current loop
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No running loop - create one
        return asyncio.run(coro)


@tool(
    name="create_cron_task",
    description=(
        "Create a new scheduled task that runs automatically at specified "
        "times. Use this when a user wants to schedule something recurring, "
        "like 'remind me every day at 6am', 'create my daily agenda at 8am', "
        "or 'check the weather every morning'. "
        "The cron_expression uses standard 5-field format: "
        "minute hour day month weekday. "
        "Examples: '0 6 * * *' (daily at 6am), '0 9 * * 1-5' (weekdays 9am), "
        "'30 8 1 * *' (1st of each month at 8:30am)."
    ),
)
def create_cron_task(
    name: str,
    instructions: str,
    cron_expression: str,
) -> str:
    """Create a new scheduled task.

    Args:
        name: A short, descriptive name for the task (e.g., 'daily-agenda').
        instructions: The message or command to execute when the task runs.
        cron_expression: Standard 5-field cron expression
            (minute hour day month weekday).

    Returns:
        Success message with task details or error message.

    Requirements:
        - 7.1: create_cron_task accepts name, instructions, cron_expression
        - 7.5: Validate inputs and return user-friendly error messages
    """
    name = name.strip() if name else ""
    instructions = instructions.strip() if instructions else ""
    cron_expression = cron_expression.strip() if cron_expression else ""

    # Validate required fields
    if not name:
        return "Please provide a name for the scheduled task."

    if not instructions:
        return "Please provide instructions for what the task should do."

    if not cron_expression:
        return (
            "Please provide a cron expression for when the task "
            "should run. Format: minute hour day month weekday. "
            "Example: '0 6 * * *' for daily at 6am."
        )

    # Check service availability
    if _cron_service is None:
        return "Cron service not available."

    if _current_user_id is None:
        return "User context not available."

    try:
        # Import here to avoid circular imports
        from app.services.cron_service import (
            CronExpressionError,
            CronTaskDuplicateError,
        )

        task = _run_async(
            _cron_service.create_task(
                user_id=_current_user_id,
                name=name,
                instructions=instructions,
                cron_expression=cron_expression,
            )
        )

        next_run = task.next_execution_at.strftime("%Y-%m-%d %H:%M UTC")

        return (
            f"✅ Created scheduled task '{name}'!\n\n"
            f"📋 Instructions: {instructions}\n"
            f"⏰ Schedule: {cron_expression}\n"
            f"🔜 Next run: {next_run}"
        )

    except CronExpressionError as e:
        return (
            f"Invalid cron expression: {str(e)}\n\n"
            "The format is: minute hour day month weekday\n"
            "Examples:\n"
            "- '0 6 * * *' = daily at 6:00 AM\n"
            "- '0 9 * * 1-5' = weekdays at 9:00 AM\n"
            "- '30 8 1 * *' = 1st of each month at 8:30 AM"
        )

    except CronTaskDuplicateError:
        return (
            f"A task named '{name}' already exists. "
            "Please choose a different name or delete the "
            "existing task first."
        )

    except Exception as e:
        logger.exception("Failed to create cron task: %s", e)
        return f"Failed to create scheduled task: {str(e)}"


@tool(
    name="list_cron_tasks",
    description=(
        "List all scheduled tasks for the current user. "
        "Use this when a user asks about their scheduled tasks, "
        "recurring tasks, or wants to see what's scheduled."
    ),
)
def list_cron_tasks() -> str:
    """List all scheduled tasks for the current user.

    Returns:
        Formatted list of tasks or message if none found.

    Requirements:
        - 7.2: list_cron_tasks tool returns all cron tasks for current user
    """
    # Check service availability
    if _cron_service is None:
        return "Cron service not available."

    if _current_user_id is None:
        return "User context not available."

    try:
        tasks = _run_async(_cron_service.list_tasks(_current_user_id))

        if not tasks:
            return (
                "📋 No scheduled tasks found.\n\n"
                "You can create one by telling me what you'd like to "
                "schedule and when."
            )

        # Format task list
        lines = ["📋 **Scheduled Tasks**\n"]

        for task in tasks:
            status = "✅ Enabled" if task.enabled else "⏸️ Disabled"
            next_run = task.next_execution_at.strftime("%Y-%m-%d %H:%M UTC")
            last_run = (
                task.last_executed_at.strftime("%Y-%m-%d %H:%M UTC")
                if task.last_executed_at
                else "Never"
            )

            lines.append(f"**{task.name}** ({status})")
            lines.append(f"  📝 {task.instructions}")
            lines.append(f"  ⏰ Schedule: {task.cron_expression}")
            lines.append(f"  🔜 Next: {next_run}")
            lines.append(f"  📅 Last: {last_run}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("Failed to list cron tasks: %s", e)
        return f"Failed to list scheduled tasks: {str(e)}"


@tool(
    name="delete_cron_task",
    description=(
        "Delete a scheduled task by its name or ID. "
        "Use this when a user wants to remove, cancel, or stop a "
        "scheduled task."
    ),
)
def delete_cron_task(task_identifier: str) -> str:
    """Delete a scheduled task by name or ID.

    Args:
        task_identifier: The name or ID of the task to delete.

    Returns:
        Success message or error message.

    Requirements:
        - 7.3: delete_cron_task tool that removes a task by name or id
        - 7.5: Validate inputs and return user-friendly error messages
    """
    task_identifier = task_identifier.strip() if task_identifier else ""

    if not task_identifier:
        return "Please specify the name or ID of the task to delete."

    # Check service availability
    if _cron_service is None:
        return "Cron service not available."

    if _current_user_id is None:
        return "User context not available."

    try:
        from app.services.cron_service import CronTaskNotFoundError

        _run_async(
            _cron_service.delete_task(
                user_id=_current_user_id,
                task_identifier=task_identifier,
            )
        )

        return f"✅ Deleted scheduled task '{task_identifier}'."

    except CronTaskNotFoundError:
        return (
            f"Task '{task_identifier}' not found. "
            "Use list_cron_tasks to see your scheduled tasks."
        )

    except Exception as e:
        logger.exception("Failed to delete cron task: %s", e)
        return f"Failed to delete scheduled task: {str(e)}"
