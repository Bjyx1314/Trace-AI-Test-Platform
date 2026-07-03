"""add requirement iteration

Revision ID: a1b2c3d4e5f6
Revises: f5a9b3c2e7d1
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f5a9b3c2e7d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('requirements', sa.Column('iteration', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('requirements', 'iteration')
