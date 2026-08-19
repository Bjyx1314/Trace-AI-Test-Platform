"""add app_step_recipe

Revision ID: s9c0d1e2f3a4
Revises: s8b9c0d1e2f3
Create Date: 2026-07-14

App 步骤操作经验：步骤判通过时记下成功操作序列，下次同 App 同步骤确定性回放。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s9c0d1e2f3a4"
down_revision: Union[str, None] = "s8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_step_recipe",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("app_pkg", sa.String(length=120), nullable=False),
        sa.Column("step_sig", sa.String(length=200), nullable=False),
        sa.Column("actions", JSONB(), nullable=True),
        sa.Column("n_actions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_pkg", "step_sig", name="uq_app_step_recipe"),
    )
    op.create_index("ix_app_step_recipe_app_pkg", "app_step_recipe", ["app_pkg"])


def downgrade() -> None:
    op.drop_index("ix_app_step_recipe_app_pkg", table_name="app_step_recipe")
    op.drop_table("app_step_recipe")
