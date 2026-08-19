"""add test_data_requirement

Revision ID: u1b2c3d4e5f6
Revises: t0a1b2c3d4e5
Create Date: 2026-07-21

测试数据准备与状态编排 MVP-0：用例的数据要求表。MVP-0 用 strategy=MANUAL + manual_values
承载人工直填的实际值，执行前置据此生成 ExecutionContext 变量注入 web/app/api。其余字段为
后续 AUTO 造数阶段预留（多为可空）。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "u1b2c3d4e5f6"
down_revision: Union[str, None] = "t0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_data_requirement",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=60), nullable=False),
        sa.Column("data_type", sa.String(length=60), nullable=True),
        sa.Column("schema_version", sa.String(length=20), nullable=True),
        sa.Column("target_state", JSONB(), nullable=True),
        sa.Column("constraints", JSONB(), nullable=True),
        sa.Column("strategy", sa.String(length=20), nullable=False, server_default="MANUAL"),
        sa.Column("reuse_policy", sa.String(length=20), nullable=False, server_default="CREATE_NEW"),
        sa.Column("isolation", sa.String(length=20), nullable=False, server_default="EXCLUSIVE"),
        sa.Column("post_state", JSONB(), nullable=True),
        sa.Column("cleanup_policy", JSONB(), nullable=True),
        sa.Column("scenario_id", sa.String(length=80), nullable=True),
        sa.Column("scenario_version", sa.String(length=20), nullable=True),
        sa.Column("output_key", sa.String(length=60), nullable=True),
        sa.Column("depends_on", JSONB(), nullable=True),
        sa.Column("manual_values", JSONB(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("approved_snapshot", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "alias", name="uq_test_data_requirement_case_alias"),
    )
    op.create_index("ix_test_data_requirement_case_id", "test_data_requirement", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_test_data_requirement_case_id", table_name="test_data_requirement")
    op.drop_table("test_data_requirement")
