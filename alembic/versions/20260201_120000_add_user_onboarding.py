"""add_user_onboarding

Revision ID: a1b2c3d4e5f6
Revises: 6da7aabbc111
Create Date: 2026-02-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '6da7aabbc111'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add agent_name column (nullable, for existing compatibility)
    op.add_column('users', sa.Column('agent_name', sa.String(), nullable=True))
    # Add onboarding_completed column with default False
    op.add_column('users', sa.Column('onboarding_completed', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'onboarding_completed')
    op.drop_column('users', 'agent_name')
