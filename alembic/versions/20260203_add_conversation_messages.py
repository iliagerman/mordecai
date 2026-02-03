"""add_conversation_messages

Revision ID: a1b2c3d4e5f6
Revises: 9f0c1a2b3c4d
Create Date: 2026-02-03 12:00:00

Adds conversation_messages table for persisting conversation history
across server restarts and isolating cron task messages.

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "9f0c1a2b3c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Conversation messages table
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "is_cron",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes for efficient queries
    op.create_index(
        "ix_conversation_messages_user_id",
        "conversation_messages",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_messages_session_id",
        "conversation_messages",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_messages_created_at",
        "conversation_messages",
        ["created_at"],
        unique=False,
    )
    # Composite index for user+session lookups
    op.create_index(
        "ix_conversation_user_session",
        "conversation_messages",
        ["user_id", "session_id"],
        unique=False,
    )
    # Composite index for user+created_at lookups
    op.create_index(
        "ix_conversation_user_created",
        "conversation_messages",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_user_created", table_name="conversation_messages")
    op.drop_index("ix_conversation_user_session", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_created_at", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_session_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_user_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
