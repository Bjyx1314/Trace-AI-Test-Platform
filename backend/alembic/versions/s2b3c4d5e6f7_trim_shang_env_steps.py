"""收敛 Android App 配方的 env_steps 为「入口一行」

选中环境+点确定已由引擎通用步(_env_select_goal)接管，配方只需描述「怎么打开环境设置入口」。
与新推荐写法一致，且重启后按 env_steps 重进环境设置校验时步数更少、更稳。

Revision ID: s2b3c4d5e6f7
Revises: s1a2b3c4d5e6
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's2b3c4d5e6f7'
down_revision: Union[str, None] = 's1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENTRY_ONLY = "当前在Android App登录页。点击屏幕【左下角的扇形图标】，进入「环境设置」页面。"
_OLD_FULL = (
    "当前应在Android App登录页。点击屏幕【左下角的扇形图标】，进入「环境设置」页面。\n"
    "当前在环境设置页面。找到并点击选择「{env}」这个环境。\n"
    "点击「确定」按钮，确认环境切换。"
)


def upgrade() -> None:
    # 仅当仍是初始 seed 的完整三行(未被人工改过)才收敛，避免覆盖用户自定义
    op.execute(sa.text(
        "UPDATE app_login_recipes SET env_steps = :new "
        "WHERE match_keywords = '商,app' AND env_steps = :old"
    ).bindparams(new=_ENTRY_ONLY, old=_OLD_FULL))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE app_login_recipes SET env_steps = :old "
        "WHERE match_keywords = '商,app' AND env_steps = :new"
    ).bindparams(old=_OLD_FULL, new=_ENTRY_ONLY))
