"""initial_schema

Revision ID: 9f0c1a2b3c4d
Revises:
Create Date: 2026-02-01 22:10:15

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "9f0c1a2b3c4d"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("telegram_id", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column(
            "onboarding_completed",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_active", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Matches ORM: telegram_id is unique + indexed
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    # Skills
    op.create_table(
        "skills",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("installed_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )

    # Tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_user_id"), "tasks", ["user_id"], unique=False)

    # Logs
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_logs_user_id"), "logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_logs_timestamp"), "logs", ["timestamp"], unique=False)

    # Long memory
    op.create_table(
        "long_memory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_user_memory_key"),
    )
    op.create_index(op.f("ix_long_memory_user_id"), "long_memory", ["user_id"], unique=False)

    # Cron tasks
    op.create_table(
        "cron_tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("cron_expression", sa.String(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_executed_at", sa.DateTime(), nullable=True),
        sa.Column("next_execution_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_user_cron_task_name"),
    )
    op.create_index(
        op.f("ix_cron_tasks_user_id"),
        "cron_tasks",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cron_tasks_next_execution_at"),
        "cron_tasks",
        ["next_execution_at"],
        unique=False,
    )

    # Cron locks
    op.create_table(
        "cron_locks",
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("lock_acquired_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["cron_tasks.id"]),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("cron_locks")

    op.drop_index(op.f("ix_cron_tasks_next_execution_at"), table_name="cron_tasks")
    op.drop_index(op.f("ix_cron_tasks_user_id"), table_name="cron_tasks")
    op.drop_table("cron_tasks")

    op.drop_index(op.f("ix_long_memory_user_id"), table_name="long_memory")
    op.drop_table("long_memory")

    op.drop_index(op.f("ix_logs_timestamp"), table_name="logs")
    op.drop_index(op.f("ix_logs_user_id"), table_name="logs")
    op.drop_table("logs")

    op.drop_index(op.f("ix_tasks_user_id"), table_name="tasks")
    op.drop_table("tasks")

    op.drop_table("skills")

    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")
