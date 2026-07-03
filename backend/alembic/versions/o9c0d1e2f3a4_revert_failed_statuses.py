"""revert legacy analysis_failed/generation_failed statuses to original

去掉了 analysis_failed/generation_failed 这两个多加的需求状态，把历史脏数据回退到原状态。

Revision ID: o9c0d1e2f3a4
Revises: n8b9c0d1e2f3
Create Date: 2026-06-30

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'o9c0d1e2f3a4'
down_revision: Union[str, None] = 'n8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for tbl in ("requirements", "requirement_slices"):
        op.execute(f"UPDATE {tbl} SET status='pending_analysis' WHERE status='analysis_failed'")
        op.execute(f"UPDATE {tbl} SET status='pending_case_generation' WHERE status='generation_failed'")


def downgrade() -> None:
    pass
