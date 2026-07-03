"""add execution_mode and runner_node to executions (merge two heads)

给 executions 加执行模式与执行机标识（详设 P1/3.2）。本迁移同时合并历史遗留的
两个 head（b3c4d5e6f7a8 / d9f0a1b2c3e4）。

Revision ID: a1c2e3f4b5d6
Revises: b3c4d5e6f7a8, d9f0a1b2c3e4
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, Sequence[str], None] = ('b3c4d5e6f7a8', 'd9f0a1b2c3e4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('executions', sa.Column('execution_mode', sa.String(length=10), nullable=False, server_default='mock'))
    op.add_column('executions', sa.Column('runner_node', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('executions', 'runner_node')
    op.drop_column('executions', 'execution_mode')
