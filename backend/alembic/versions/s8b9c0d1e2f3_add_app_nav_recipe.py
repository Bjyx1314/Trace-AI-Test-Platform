"""add app_nav_recipe

Revision ID: s8b9c0d1e2f3
Revises: s7a8b9c0d1e2
Create Date: 2026-07-14

App 导航路径缓存：记录到达某目标页/入口的成功导航经验，供下次直接回放/提示，
避免每次 AI 视觉从零盲滑找入口。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "s8b9c0d1e2f3"
down_revision: Union[str, None] = "s7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_nav_recipe",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("app_pkg", sa.String(length=120), nullable=False),
        sa.Column("target", sa.String(length=80), nullable=False),
        sa.Column("entry", sa.String(length=80), nullable=True),
        sa.Column("path", JSONB(), nullable=True),
        sa.Column("swipes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("direction", sa.String(length=10), nullable=False, server_default="up"),
        sa.Column("near_text", sa.String(length=120), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_pkg", "target", name="uq_app_nav_recipe"),
    )
    op.create_index("ix_app_nav_recipe_app_pkg", "app_nav_recipe", ["app_pkg"])


def downgrade() -> None:
    op.drop_index("ix_app_nav_recipe_app_pkg", table_name="app_nav_recipe")
    op.drop_table("app_nav_recipe")
