"""add executions.case_ids

Revision ID: s7a8b9c0d1e2
Revises: s6f7a8b9c0d1
Create Date: 2026-07-14

执行覆盖的用例 id 列表落库，供任意查看者从服务端还原「正在执行的用例」状态
（此前仅存发起人 localStorage，别人看不到执行状态）。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s7a8b9c0d1e2"
down_revision: Union[str, None] = "s6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("case_ids", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "case_ids")
