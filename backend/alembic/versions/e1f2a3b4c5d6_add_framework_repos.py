"""add framework_repos (框架仓库登记表)

把"已有自动化框架的 git 仓库"绑定到平台，承载 索引驱动生成 + 仓库内执行 模型的地基。

Revision ID: e1f2a3b4c5d6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'framework_repos',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('repo_type', sa.String(length=20), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('git_url', sa.String(length=500), nullable=False),
        sa.Column('branch', sa.String(length=100), nullable=False, server_default='main'),
        sa.Column('local_path', sa.String(length=500), nullable=True),
        sa.Column('tests_root', sa.String(length=300), nullable=True),
        sa.Column('data_root', sa.String(length=300), nullable=True),
        sa.Column('keyword_root', sa.String(length=300), nullable=True),
        sa.Column('run_command', sa.Text(), nullable=True),
        sa.Column('install_command', sa.Text(), nullable=True),
        sa.Column('env_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('index_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('index_status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('index_commit', sa.String(length=60), nullable=True),
        sa.Column('indexed_at', sa.DateTime(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_framework_repos_project_id', 'framework_repos', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_framework_repos_project_id', table_name='framework_repos')
    op.drop_table('framework_repos')
