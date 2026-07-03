"""add pending_scope to requirement_slices (增量分析)

Revision ID: n8b9c0d1e2f3
Revises: m7a8b9c0d1e2
Create Date: 2026-06-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'n8b9c0d1e2f3'
down_revision: Union[str, None] = 'm7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('requirement_slices', sa.Column('pending_scope', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('requirement_slices', 'pending_scope')
