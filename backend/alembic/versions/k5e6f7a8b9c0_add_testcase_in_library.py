"""add in_library to test_cases (用例库纳入规则)

用例库直接导入/手工新增的用例直接入库；需求侧生成/导入的用例执行通过后才纳入。
新增 in_library 标记，并回填历史数据：requirement_id 为空(库直接来源) 或 已通过(passed/manual_passed) 的置 True。

Revision ID: k5e6f7a8b9c0
Revises: j4d5e6f7a8b9
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k5e6f7a8b9c0'
down_revision: Union[str, None] = 'j4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column(
        'in_library', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    # 回填：库直接来源(无 requirement_id) 或 已通过 → 纳入库
    op.execute(
        "UPDATE test_cases SET in_library = TRUE "
        "WHERE requirement_id IS NULL OR last_status IN ('passed', 'manual_passed')"
    )


def downgrade() -> None:
    op.drop_column('test_cases', 'in_library')
