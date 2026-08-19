"""user_fav_phones 加 kind(phone/tenant) —— 常用号码与常用租户复用一张表

Revision ID: s6f7a8b9c0d1
Revises: s5e6f7a8b9c0
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 's6f7a8b9c0d1'
down_revision: Union[str, None] = 's5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_fav_phones', sa.Column('kind', sa.String(length=20), server_default='phone', nullable=False))
    op.alter_column('user_fav_phones', 'phone', type_=sa.String(length=80))  # 也存租户名，放宽
    op.drop_constraint('uq_fav_phone_user_phone', 'user_fav_phones', type_='unique')
    op.create_unique_constraint('uq_fav_user_kind_value', 'user_fav_phones', ['user_sub', 'kind', 'phone'])


def downgrade() -> None:
    op.drop_constraint('uq_fav_user_kind_value', 'user_fav_phones', type_='unique')
    op.create_unique_constraint('uq_fav_phone_user_phone', 'user_fav_phones', ['user_sub', 'phone'])
    op.drop_column('user_fav_phones', 'kind')
