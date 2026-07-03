"""add owner_name to requirements (需求归属人=添加/同步者)

Revision ID: l6f7a8b9c0d1
Revises: k5e6f7a8b9c0
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l6f7a8b9c0d1'
down_revision: Union[str, None] = 'k5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('requirements', sa.Column('owner_name', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('requirements', 'owner_name')
