"""add page structure cache table (chapter 7.3)

Revision ID: f5a9b3c2e7d1
Revises: d4e8f3a9c2b1
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f5a9b3c2e7d1'
down_revision: Union[str, None] = 'd4e8f3a9c2b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'page_structure_caches',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('url_pattern', sa.String(500), nullable=False),
        sa.Column('page_name', sa.String(200), nullable=False),
        sa.Column('dom_hash', postgresql.JSONB(), nullable=True),
        sa.Column('regions', postgresql.JSONB(), nullable=True),
        sa.Column('captured_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('last_hit_at', sa.DateTime(), nullable=True),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
    )
    op.create_index('ix_page_structure_caches_project_id', 'page_structure_caches', ['project_id'])
    op.create_index('ix_page_structure_caches_url_pattern', 'page_structure_caches', ['url_pattern'])


def downgrade() -> None:
    op.drop_index('ix_page_structure_caches_url_pattern', table_name='page_structure_caches')
    op.drop_index('ix_page_structure_caches_project_id', table_name='page_structure_caches')
    op.drop_table('page_structure_caches')
