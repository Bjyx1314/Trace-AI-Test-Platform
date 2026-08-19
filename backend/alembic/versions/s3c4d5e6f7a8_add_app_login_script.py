"""add app_login_scripts (登录动作轨迹缓存，供确定性回放)

视觉登录成功后录下动作轨迹，按 (app_package,width,height) 唯一；下次登录先回放，省 token 更稳。

Revision ID: s3c4d5e6f7a8
Revises: s2b3c4d5e6f7
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 's3c4d5e6f7a8'
down_revision: Union[str, None] = 's2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_login_scripts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('app_package', sa.String(length=200), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('script', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_package', 'width', 'height', name='uq_login_script_pkg_res'),
    )


def downgrade() -> None:
    op.drop_table('app_login_scripts')
