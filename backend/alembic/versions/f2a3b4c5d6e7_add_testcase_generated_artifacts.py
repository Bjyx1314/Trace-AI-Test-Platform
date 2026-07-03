"""add framework_repo_id + generated_artifacts to test_cases

用例生成产物从"单段自包含脚本"升级为"框架原生多文件产物"，记录归属框架仓库与文件集。

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-22 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column('framework_repo_id', sa.String(length=36), nullable=True))
    op.add_column('test_cases', sa.Column('generated_artifacts', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('test_cases', 'generated_artifacts')
    op.drop_column('test_cases', 'framework_repo_id')
