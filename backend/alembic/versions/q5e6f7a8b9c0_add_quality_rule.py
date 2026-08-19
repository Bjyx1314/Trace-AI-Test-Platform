"""add quality_rules (第一层硬规则引擎)

方案 6.1：确定性硬规则。MVP 从 skill reference.md blast-radius 导入只读初始集(seed 脚本)，无编辑界面。

Revision ID: q5e6f7a8b9c0
Revises: q4d5e6f7a8b9
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'q5e6f7a8b9c0'
down_revision: Union[str, None] = 'q4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'quality_rules',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('match_tags', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'),
        sa.Column('min_priority', sa.String(length=10), nullable=True),
        sa.Column('required_covered_items', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('source', sa.String(length=300), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('quality_rules')
