"""add testcase is_automated

Revision ID: 7f3c9a1b2d4e
Revises: aec91f6ed4f2
Create Date: 2026-06-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f3c9a1b2d4e'
down_revision: Union[str, None] = 'aec91f6ed4f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column('is_automated', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('test_cases', 'is_automated')
