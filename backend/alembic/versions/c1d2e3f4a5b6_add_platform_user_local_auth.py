"""add local account fields to platform_users

本地账号密码登录：username/password_hash/is_active/auth_source，
并把 external_user_id 改为可空（本地账号无 SSO id）。

Revision ID: c1d2e3f4a5b6
Revises: f2a3b4c5d6e7
Create Date: 2026-06-23 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('platform_users', sa.Column('username', sa.String(length=100), nullable=True))
    op.add_column('platform_users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('platform_users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('platform_users', sa.Column('auth_source', sa.String(length=20), nullable=False, server_default='external'))
    op.create_index('ix_platform_users_username', 'platform_users', ['username'], unique=True)
    op.alter_column('platform_users', 'external_user_id', existing_type=sa.String(length=200), nullable=True)


def downgrade() -> None:
    op.alter_column('platform_users', 'external_user_id', existing_type=sa.String(length=200), nullable=False)
    op.drop_index('ix_platform_users_username', table_name='platform_users')
    op.drop_column('platform_users', 'auth_source')
    op.drop_column('platform_users', 'is_active')
    op.drop_column('platform_users', 'password_hash')
    op.drop_column('platform_users', 'username')
