"""框架仓库登记种子（幂等，可重复执行）。

用法:
    python -m app.seed_frameworks

说明:
  - 仅当配置 FRAMEWORK_WEB_GIT_URL / FRAMEWORK_API_GIT_URL 时登记外部框架。
  - 路径取容器内挂载点（环境变量覆盖）：
      FRAMEWORK_ROOT            web_ui_automation 挂载点（PC Web + 移动端共用）默认 /opt/framework
      INTERFACE_FRAMEWORK_ROOT  interfaceauto2.0 挂载点，默认 /opt/framework-inter
  - 幂等：按 name 匹配，已存在则同步关键字段（git_url/branch/local_path/各 root/run_command），
    不存在则插入；不在清单内的其它注册一律保留，绝不删除。
"""
from __future__ import annotations
import asyncio
import os

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import FrameworkRepo

_WEB = os.environ.get("FRAMEWORK_ROOT") or "/opt/framework"
_INTER = os.environ.get("INTERFACE_FRAMEWORK_ROOT") or "/opt/framework-inter"

_GIT_WEB = os.environ.get("FRAMEWORK_WEB_GIT_URL")
_GIT_INTER = os.environ.get("FRAMEWORK_API_GIT_URL")
_BRANCH = os.environ.get("FRAMEWORK_GIT_BRANCH") or "main"

# name -> 字段（与本地登记一比一，local_path 改为服务器容器内挂载点）
SEED: list[dict] = []

if _GIT_INTER:
    SEED.append({
        "name": "接口自动化", "repo_type": "interface",
        "description": "外部接口自动化框架",
        "git_url": _GIT_INTER, "branch": _BRANCH, "local_path": _INTER,
        "tests_root": "cases", "data_root": "data", "keyword_root": "data/aw/aw_class",
        "run_command": "pytest {target} -q",
    })

if _GIT_WEB:
    SEED.extend([{
        "name": "PC Web自动化", "repo_type": "web",
        "description": "外部 PC Web 自动化框架",
        "git_url": _GIT_WEB, "branch": _BRANCH, "local_path": _WEB,
        "tests_root": "ui_web/tests", "data_root": None, "keyword_root": None,
        "run_command": "pytest {target} -m smoke",
    },
    {
        "name": "移动端自动化", "repo_type": "app",
        "description": "外部移动端自动化框架",
        "git_url": _GIT_WEB, "branch": _BRANCH, "local_path": _WEB,
        "tests_root": "ui_app/tests", "data_root": None, "keyword_root": None,
        "run_command": "pytest {target} -m smoke",
    }])

_SYNC_FIELDS = ("repo_type", "description", "git_url", "branch", "local_path",
                "tests_root", "data_root", "keyword_root", "run_command")


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        inserted = synced = 0
        for item in SEED:
            existing = (await db.execute(
                select(FrameworkRepo).where(FrameworkRepo.name == item["name"])
            )).scalar_one_or_none()
            if existing is not None:
                changed = False
                for f in _SYNC_FIELDS:
                    if getattr(existing, f) != item.get(f):
                        setattr(existing, f, item.get(f))
                        changed = True
                if changed:
                    synced += 1
                continue
            db.add(FrameworkRepo(**item))
            inserted += 1
        await db.commit()
        print(f"框架注册同步完成：新增 {inserted} 条，同步 {synced} 条；"
              f"web/app→{_WEB} interface→{_INTER}")


if __name__ == "__main__":
    asyncio.run(seed())
