"""add app_login_recipes (App 自动登录配方，配置驱动)

替代硬编码在 app_login.py 的每端 goals。各 App 只在「选环境/选租户」上不同，
用一行一步的自然语言 env_steps 描述，启动页/引导页由通用前置目标自动趟过。
顺带 seed Android App 配方，保证从硬编码切到配置驱动后登录不回归。

Revision ID: s1a2b3c4d5e6
Revises: r6f7a8b9c0d1
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's1a2b3c4d5e6'
down_revision: Union[str, None] = 'r6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SHANG_ENV_STEPS = (
    "当前应在Android App登录页。点击屏幕【左下角的扇形图标】，进入「环境设置」页面。\n"
    "当前在环境设置页面。找到并点击选择「{env}」这个环境。\n"
    "点击「确定」按钮，确认环境切换。"
)


def upgrade() -> None:
    op.create_table(
        'app_login_recipes',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('match_keywords', sa.String(length=300), nullable=False),
        sa.Column('env_steps', sa.Text(), nullable=True),
        sa.Column('restart_after_env', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('needs_tenant', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # seed Android App 配方（match "商"+"app"，选环境→重启，需选租户），复刻原硬编码行为
    op.execute(sa.text(
        "INSERT INTO app_login_recipes "
        "(id, name, match_keywords, env_steps, restart_after_env, needs_tenant, enabled) "
        "VALUES (:id, :name, :kw, :steps, true, true, true)"
    ).bindparams(
        id='a0000000-0000-0000-0000-000000000001',
        name='Android App',
        kw='商,app',
        steps=_SHANG_ENV_STEPS,
    ))


def downgrade() -> None:
    op.drop_table('app_login_recipes')
