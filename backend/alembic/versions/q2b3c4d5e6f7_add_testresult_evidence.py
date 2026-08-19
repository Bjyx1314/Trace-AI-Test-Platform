"""add covered-item execution evidence to test_results (AI质量闭环: 执行证据)

方案 12.1：执行结果记录覆盖项级证据 checked_points，及运行时实际访问页面/接口调用。

Revision ID: q2b3c4d5e6f7
Revises: q1a2b3c4d5e6
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'q2b3c4d5e6f7'
down_revision: Union[str, None] = 'q1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_results', sa.Column('checked_points', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('test_results', sa.Column('actual_visited_pages', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('test_results', sa.Column('actual_api_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('test_results', 'actual_api_calls')
    op.drop_column('test_results', 'actual_visited_pages')
    op.drop_column('test_results', 'checked_points')
