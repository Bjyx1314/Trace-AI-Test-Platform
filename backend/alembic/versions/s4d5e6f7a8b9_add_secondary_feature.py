"""add test_cases.secondary_feature (二级功能分组标签，从需求原文提取，独立于 source_issue_point)

用例脑图「需求→二级功能→用例」的中间层。此前退化用 source_issue_point 分组，
但 source_issue_point 是增量重生成去重的依据、且被 LLM 乱填 → 分组碎。
改为独立字段：从需求【原文】提取页面级二级功能并回填，绝不动 source_issue_point。

Revision ID: s4d5e6f7a8b9
Revises: s3c4d5e6f7a8
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 's4d5e6f7a8b9'
down_revision: Union[str, None] = 's3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column('secondary_feature', sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column('test_cases', 'secondary_feature')
