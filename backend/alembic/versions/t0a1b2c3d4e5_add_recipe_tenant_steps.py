"""add app_login_recipes.tenant_steps

Revision ID: t0a1b2c3d4e5
Revises: s9c0d1e2f3a4
Create Date: 2026-07-14

自定义切租户/租户流程(可空)。空=默认左上角租户流程；有值=按此步骤切(如Android App：我的→租户列表)。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "t0a1b2c3d4e5"
down_revision: Union[str, None] = "s9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_login_recipes", sa.Column("tenant_steps", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_login_recipes", "tenant_steps")
