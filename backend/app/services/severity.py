"""缺陷等级（severity）工具。

缺陷等级取自枚举管理 category="severity"，由用户维护；sort_order 升序 = 由重到轻。
最高级（首个）作为发布门禁的「致命缺陷」阻断判定级别。无枚举数据时回退到内置 4 级。
注意：与「用例优先级 priority(P0/P1/P2)」是两套独立枚举，互不混用。
"""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import EnumDefinition

# 内置缺陷等级（与 seed_enums.py category=severity 保持一致），仅在枚举表为空时回退
DEFAULT_SEVERITY_LEVELS = ["1级-致命", "2级-严重", "3级-一般", "4级-轻微"]
# 新建缺陷的默认等级（取中间偏轻）
DEFAULT_SEVERITY = "3级-一般"


async def get_severity_levels(db: AsyncSession) -> list[str]:
    """返回缺陷等级 key 列表，由重到轻；枚举为空时回退内置 4 级。"""
    levels = [
        r.key for r in (await db.execute(
            select(EnumDefinition).where(EnumDefinition.category == "severity")
            .order_by(EnumDefinition.sort_order)
        )).scalars().all()
    ]
    return levels or list(DEFAULT_SEVERITY_LEVELS)


async def get_blocking_severity(db: AsyncSession) -> str:
    """发布门禁阻断级别 = 最高（最重）缺陷等级。"""
    return (await get_severity_levels(db))[0]
