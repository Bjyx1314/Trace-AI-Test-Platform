"""add user_fav_phones (每个用户的常用登录手机号，PC/App 通用)

执行弹框输入手机号时可「加入常用」，常用号展示在默认号上方、可删；按用户(JWT sub)隔离，
同一账号下 PC 与 App 通用。

Revision ID: s5e6f7a8b9c0
Revises: s4d5e6f7a8b9
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 's5e6f7a8b9c0'
down_revision: Union[str, None] = 's4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_fav_phones',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_sub', sa.String(length=200), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_sub', 'phone', name='uq_fav_phone_user_phone'),
    )
    op.create_index('ix_fav_phone_user', 'user_fav_phones', ['user_sub'])


def downgrade() -> None:
    op.drop_index('ix_fav_phone_user', table_name='user_fav_phones')
    op.drop_table('user_fav_phones')
