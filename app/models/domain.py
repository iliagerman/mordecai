"""Pydantic domain models.

These models are returned by DAOs and used throughout the service layer.
They provide strong typing and validation. SQLAlchemy ORM objects should
never be exposed outside the DAO layer - always convert to these models.
"""

from datetime import datetime

from app.enums import LogSeverity, TaskStatus
from app.models.base import JsonModel


class User(JsonModel):
    """User domain model.

    Represents a user identified by their Telegram ID.
    """

    id: str
    telegram_id: str
    agent_name: str | None = None  # Custom name for the agent
    onboarding_completed: bool = False
    created_at: datetime
    last_active: datetime


class Task(JsonModel):
    """Task domain model.

    Represents a kanban task with status tracking.
    """

    id: str
    user_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime
    updated_at: datetime


class LogEntry(JsonModel):
    """Log entry domain model.

    Represents an agent activity log entry.
    """

    id: int | None = None
    user_id: str
    action: str
    severity: LogSeverity = LogSeverity.INFO
    details: dict | None = None
    timestamp: datetime


class LongMemory(JsonModel):
    """Long memory domain model.

    Represents a persistent key-value memory entry.
    """

    id: int | None = None
    user_id: str
    key: str
    value: str
    updated_at: datetime


class SkillMetadata(JsonModel):
    """Skill metadata domain model.

    Represents metadata about an installed skill.
    """

    name: str
    source_url: str
    installed_at: datetime
    version: str | None = None


class CronTask(JsonModel):
    """Cron task domain model.

    Represents a scheduled task with cron expression defining when to execute.
    """

    id: str
    user_id: str
    name: str
    instructions: str
    cron_expression: str
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    last_executed_at: datetime | None = None
    next_execution_at: datetime


class CronLock(JsonModel):
    """Cron lock domain model.

    Represents a distributed lock for cron task execution.
    """

    task_id: str
    instance_id: str
    lock_acquired_at: datetime
