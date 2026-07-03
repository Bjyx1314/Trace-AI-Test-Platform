"""add requirement analysis_confirmation

Revision ID: b2f6d4e8a1c3
Revises: 7f3c9a1b2d4e
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2f6d4e8a1c3'
down_revision: Union[str, None] = '7f3c9a1b2d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('requirements', sa.Column('analysis_confirmation', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('requirements', 'analysis_confirmation')
