"""add browser_cookies table

Revision ID: a3b4c5d6e7f8
Revises: 2567797359b1
Create Date: 2026-02-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = '2567797359b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'browser_cookies',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('domain', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('path', sa.String(), nullable=False, server_default='/'),
        sa.Column('expires', sa.DateTime(), nullable=True),
        sa.Column('http_only', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('secure', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('same_site', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'domain', 'name', 'path', name='uq_user_cookie'),
    )
    op.create_index('ix_browser_cookie_user_id', 'browser_cookies', ['user_id'])
    op.create_index('ix_browser_cookie_user_domain', 'browser_cookies', ['user_id', 'domain'])


def downgrade() -> None:
    op.drop_index('ix_browser_cookie_user_domain', table_name='browser_cookies')
    op.drop_index('ix_browser_cookie_user_id', table_name='browser_cookies')
    op.drop_table('browser_cookies')
