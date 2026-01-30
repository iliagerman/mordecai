"""StrEnum definitions for type-safe constants."""

from enum import StrEnum


class ModelProvider(StrEnum):
    """Supported AI model providers."""

    BEDROCK = "bedrock"
    OPENAI = "openai"
    GOOGLE = "google"


class TaskStatus(StrEnum):
    """Kanban task status values."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class CommandType(StrEnum):
    """User command types."""

    NEW = "new"
    LOGS = "logs"
    INSTALL_SKILL = "install_skill"
    UNINSTALL_SKILL = "uninstall_skill"
    HELP = "help"
    MESSAGE = "message"


class LogSeverity(StrEnum):
    """Log severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WebhookEventType(StrEnum):
    """Webhook event types."""

    TASK_CREATED = "task_created"
    EXTERNAL_TRIGGER = "external_trigger"
