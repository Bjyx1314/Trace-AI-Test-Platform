"""默认项目种子（幂等）。

用法:
    python -m app.seed_default_project

目的：平台的需求/用例/执行/页面缓存等都挂在项目下，但不该强制用户先建项目。
当系统里【一个项目都没有】时，自动建一个「默认项目」，前端会自动选中它，
于是「同步/上传需求」「页面缓存选地址」等不再被项目门槛卡住。
已存在任意项目则原样跳过，绝不新增/覆盖。
"""
from __future__ import annotations
import asyncio

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Project, QualityGateConfig

DEFAULT_PROJECT_ID = "demo-project"


async def ensure_default_project(db) -> bool:
    existing = (await db.execute(select(Project.id).limit(1))).scalar_one_or_none()
    if existing:
        return False
    project = Project(
        id=DEFAULT_PROJECT_ID,
        name="示例项目",
        description="系统默认示例项目（自动创建，可在项目设置中改名/调整前缀）",
        case_id_prefix="DEMO",
    )
    db.add(project)
    db.add(QualityGateConfig(project_id=project.id))
    await db.commit()
    return True


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        created = await ensure_default_project(db)
        print("已创建默认项目（示例项目 / 用例前缀 DEMO）" if created else "已有项目，跳过默认项目创建")


if __name__ == "__main__":
    asyncio.run(seed())
