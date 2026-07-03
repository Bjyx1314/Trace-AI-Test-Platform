"""drop platform end 'api' and clean it out of testcase platforms

api 不是「端」，只是用例类型(case_type=api)。历史误把 api 塞进 platform 端枚举，
导致「用例库按端分布」凭空多出一个 api 端。此迁移：
- 删除 platform 分类下 key='api' 的枚举端；
- 从 test_cases.platforms 数组里移除 'api'（不影响 case_type='api' 的判定）。

Revision ID: p0d1e2f3a4b5
Revises: o9c0d1e2f3a4
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'p0d1e2f3a4b5'
down_revision: Union[str, None] = 'o9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 删除误登记的 platform 端 'api'（仅 platform 分类，保留 case_type='api'）
    op.execute("DELETE FROM enum_definitions WHERE category='platform' AND key='api'")
    # 从存量用例的 platforms 数组里剔除 'api'
    op.execute("UPDATE test_cases SET platforms = array_remove(platforms, 'api') WHERE 'api' = ANY(platforms)")


def downgrade() -> None:
    pass
