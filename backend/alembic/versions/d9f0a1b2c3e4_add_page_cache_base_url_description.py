"""add base_url and description to page_structure_caches

Revision ID: d9f0a1b2c3e4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9f0a1b2c3e4'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('page_structure_caches', sa.Column('base_url', sa.String(length=300), nullable=True))
    op.add_column('page_structure_caches', sa.Column('description', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('page_structure_caches', 'description')
    op.drop_column('page_structure_caches', 'base_url')
