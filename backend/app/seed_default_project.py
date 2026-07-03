"""开源版示例项目种子（幂等）。

用法:
    python -m app.seed_default_project

平台的需求、用例、执行和页面缓存都需要项目上下文。项目表为空时创建一个
不含业务数据的「示例项目」，前端会自动选中它；已存在任意项目时原样跳过。
"""
from __future__ import annotations
import asyncio

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models import Project, QualityGateConfig


DEFAULT_PROJECT_ID = "00000000-0000-4000-8000-000000000001"


async def ensure_default_project(db: AsyncSession) -> bool:
    """项目表为空时创建通用示例项目，返回本次是否创建。"""
    existing_id = (await db.execute(select(Project.id).limit(1))).scalar_one_or_none()
    if existing_id is not None:
        return False

    project = Project(
        id=DEFAULT_PROJECT_ID,
        name="示例项目",
        description="开源版自动创建的通用项目，可直接改名或删除。",
        product_line="core",
        case_id_prefix="DEMO",
    )
    db.add(project)
    db.add(QualityGateConfig(project_id=project.id))
    try:
        await db.commit()
    except IntegrityError:
        # 多进程同时启动时固定主键只允许一方创建成功。
        await db.rollback()
        return False
    return True


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        created = await ensure_default_project(db)
        if created:
            print("已创建示例项目（示例项目 / 用例前缀 DEMO）")
        else:
            print("已有项目，跳过示例项目创建")


if __name__ == "__main__":
    asyncio.run(seed())
