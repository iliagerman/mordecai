"""add_cron_tables

Revision ID: 6da7aabbc111
Revises: 4c926c8a125a
Create Date: 2026-01-29 07:37:37.068957

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6da7aabbc111'
down_revision: Union[str, None] = '4c926c8a125a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cron_tasks',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('cron_expression', sa.String(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_executed_at', sa.DateTime(), nullable=True),
        sa.Column('next_execution_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_user_cron_task_name')
    )
    op.create_index(
        op.f('ix_cron_tasks_next_execution_at'),
        'cron_tasks',
        ['next_execution_at'],
        unique=False
    )
    op.create_index(
        op.f('ix_cron_tasks_user_id'),
        'cron_tasks',
        ['user_id'],
        unique=False
    )
    op.create_table(
        'cron_locks',
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('instance_id', sa.String(), nullable=False),
        sa.Column('lock_acquired_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['cron_tasks.id']),
        sa.PrimaryKeyConstraint('task_id')
    )


def downgrade() -> None:
    op.drop_table('cron_locks')
    op.drop_index(op.f('ix_cron_tasks_user_id'), table_name='cron_tasks')
    op.drop_index(
        op.f('ix_cron_tasks_next_execution_at'),
        table_name='cron_tasks'
    )
    op.drop_table('cron_tasks')
