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
    FORGET = "forget"
    FORGET_DELETE = "forget_delete"
    HELP = "help"
    MESSAGE = "message"
    CONVERSATION = "conversation"


class ConversationStatus(StrEnum):
    """Multi-agent conversation status values."""

    ACTIVE = "active"
    CONSENSUS_REACHED = "consensus_reached"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"
    CANCELLED = "cancelled"


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
