"""add business_repos (被测业务代码仓库登记)

方案 8.2/14.1：与 FrameworkRepo 严格区分。供代码影响分析 clone/checkout 后直跑 skill。
token 可空以支持手动「粘贴 diff / 本地路径」模式。

Revision ID: q3c4d5e6f7a8
Revises: q2b3c4d5e6f7
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'q3c4d5e6f7a8'
down_revision: Union[str, None] = 'q2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'business_repos',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('git_url', sa.String(length=500), nullable=False),
        sa.Column('default_branch', sa.String(length=100), nullable=False, server_default='master'),
        sa.Column('token', sa.String(length=500), nullable=True),
        sa.Column('workspace_path', sa.String(length=500), nullable=True),
        sa.Column('clone_depth', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('business_repos')
