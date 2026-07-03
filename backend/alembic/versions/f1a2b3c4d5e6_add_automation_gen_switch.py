"""add automation_gen_switches (merge heads)

按端控制"执行测试通过后是否生成自动化用例"的开关表。同时合并历史双 head
（a1c2e3f4b5d6 / d9f0a1b2c3e4）为单一 head。

Revision ID: f1a2b3c4d5e6
Revises: a1c2e3f4b5d6, d9f0a1b2c3e4
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = ('a1c2e3f4b5d6', 'd9f0a1b2c3e4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'automation_gen_switches',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('platform', sa.String(length=40), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('updated_by', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_automation_gen_switches_platform'),
        'automation_gen_switches',
        ['platform'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_automation_gen_switches_platform'),
        table_name='automation_gen_switches',
    )
    op.drop_table('automation_gen_switches')
