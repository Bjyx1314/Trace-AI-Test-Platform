"""add testcase review_status and similar_case_id

Revision ID: c3e7f2a9d1b5
Revises: e5c9f2a1b3d6
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e7f2a9d1b5'
down_revision: Union[str, None] = 'e5c9f2a1b3d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column('review_status', sa.String(length=30), nullable=True))
    op.add_column('test_cases', sa.Column('similar_case_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column('test_cases', 'similar_case_id')
    op.drop_column('test_cases', 'review_status')
