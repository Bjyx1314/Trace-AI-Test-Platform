"""add covered_items & sources to test_cases (AI质量闭环: 用例覆盖项)

方案 7.2/10.2：用例带 covered_items 覆盖项、来源、风险标签、命中规则、影响面节点、回归标记。
covered_items 内嵌 JSONB，MVP 不建独立表。

Revision ID: q1a2b3c4d5e6
Revises: p0d1e2f3a4b5
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'q1a2b3c4d5e6'
down_revision: Union[str, None] = 'p0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column('covered_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'))
    op.add_column('test_cases', sa.Column('sources', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'))
    op.add_column('test_cases', sa.Column('risk_tags', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'))
    op.add_column('test_cases', sa.Column('matched_rules', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'))
    op.add_column('test_cases', sa.Column('reason', sa.Text(), nullable=True))
    op.add_column('test_cases', sa.Column('affected_page_nodes', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('test_cases', sa.Column('affected_api_nodes', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('test_cases', sa.Column('regression_flag', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('test_cases', 'regression_flag')
    op.drop_column('test_cases', 'affected_api_nodes')
    op.drop_column('test_cases', 'affected_page_nodes')
    op.drop_column('test_cases', 'reason')
    op.drop_column('test_cases', 'matched_rules')
    op.drop_column('test_cases', 'risk_tags')
    op.drop_column('test_cases', 'sources')
    op.drop_column('test_cases', 'covered_items')
