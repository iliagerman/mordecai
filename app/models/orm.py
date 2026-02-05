"""SQLAlchemy ORM models.

These models define the database schema. DAOs convert these to Pydantic
domain models before returning to services - SQLAlchemy objects should
never leak outside the DAO layer.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import LogSeverity, TaskStatus


class UserModel(Base):
    """User ORM model.

    Represents a user identified by their Telegram ID.
    """

    __tablename__ = "users"

    id = Column(String, primary_key=True)
    telegram_id = Column(String, unique=True, nullable=False, index=True)
    agent_name = Column(String, nullable=True)  # Custom name for the agent
    onboarding_completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_active = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    tasks = relationship(
        "TaskModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    logs = relationship(
        "LogModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    memories = relationship(
        "LongMemoryModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    cron_tasks = relationship(
        "CronTaskModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    conversation_messages = relationship(
        "ConversationMessageModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class TaskModel(Base):
    """Task ORM model.

    Represents a kanban task with status tracking.
    """

    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    status = Column(
        String,
        nullable=False,
        default=TaskStatus.PENDING.value,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    user = relationship("UserModel", back_populates="tasks")


class LogModel(Base):
    """Log ORM model.

    Stores agent activity logs for debugging and user visibility.
    """

    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    action = Column(String, nullable=False)
    severity = Column(
        String,
        nullable=False,
        default=LogSeverity.INFO.value,
    )
    details = Column(Text)  # JSON-encoded dict
    timestamp = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    # Relationships
    user = relationship("UserModel", back_populates="logs")


class LongMemoryModel(Base):
    """Long-term memory ORM model.

    Stores persistent key-value memory entries per user.
    """

    __tablename__ = "long_memory"

    # One value per key per user.
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_memory_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    user = relationship("UserModel", back_populates="memories")


class SkillMetadataModel(Base):
    """Skill metadata ORM model.

    Tracks installed skills and their source URLs.
    """

    __tablename__ = "skills"

    name = Column(String, primary_key=True)
    source_url = Column(String, nullable=False)
    installed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    version = Column(String)


class CronTaskModel(Base):
    """Cron task ORM model.

    Represents a scheduled task with cron expression defining when to execute.
    """

    __tablename__ = "cron_tasks"

    id = Column(String, primary_key=True)
    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    name = Column(String, nullable=False)
    instructions = Column(Text, nullable=False)
    cron_expression = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    last_executed_at = Column(DateTime, nullable=True)
    next_execution_at = Column(DateTime, nullable=False, index=True)

    # Relationships
    user = relationship("UserModel", back_populates="cron_tasks")

    # Unique constraint: one task name per user
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_cron_task_name"),)


class CronLockModel(Base):
    """Cron lock ORM model for distributed locking.

    Prevents duplicate execution across multiple instances.
    """

    __tablename__ = "cron_locks"

    task_id = Column(
        String,
        ForeignKey("cron_tasks.id"),
        primary_key=True,
    )
    instance_id = Column(String, nullable=False)
    lock_acquired_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


class ConversationMessageModel(Base):
    """Conversation message ORM model.

    Stores conversation messages for persistence and recovery.
    Allows reconstruction of conversation state across restarts.
    Cron task messages are stored separately (is_cron=True) so they don't
    pollute the main user conversation when loaded.
    """

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    session_id = Column(String, nullable=False, index=True)  # For grouping by session
    role = Column(String, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    # Optional JSON-encoded structured content blocks for exact conversation reconstruction.
    # This allows storing toolUse/toolResult blocks and other rich content formats.
    # NOTE: Stored as TEXT for cross-dialect compatibility (SQLite/Postgres). DAOs
    # are responsible for JSON encoding/decoding and redaction.
    content_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )
    is_cron = Column(Boolean, nullable=False, default=False)  # Flag for cron task messages

    # Relationships
    user = relationship("UserModel", back_populates="conversation_messages")

    # Indexes for efficient queries
    __table_args__ = (
        Index("ix_conversation_user_session", "user_id", "session_id"),
        Index("ix_conversation_user_created", "user_id", "created_at"),
    )
