"""add page_cache_diffs table

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'page_cache_diffs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('cache_id', sa.String(length=36), nullable=True),
        sa.Column('url_pattern', sa.String(length=500), nullable=False),
        sa.Column('page_name', sa.String(length=200), nullable=False),
        sa.Column('changed_regions', JSONB, nullable=True),
        sa.Column('new_regions', JSONB, nullable=True),
        sa.Column('new_dom_hash', JSONB, nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('resolved_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table('page_cache_diffs')
