"""add platform_users table

Revision ID: b3c4d5e6f7a8
Revises: c8d9e0f1a2b3
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('external_user_id', sa.String(200), nullable=False, unique=True),
        sa.Column('email', sa.String(200), nullable=True),
        sa.Column('name', sa.String(200), nullable=True),
        sa.Column('role', sa.String(20), nullable=False, server_default='user'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_platform_users_external_user_id', 'platform_users', ['external_user_id'])


def downgrade() -> None:
    op.drop_index('ix_platform_users_external_user_id', 'platform_users')
    op.drop_table('platform_users')
