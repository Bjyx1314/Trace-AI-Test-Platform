"""自动化用例生成开关 —— 按"端"(脚本类型)控制是否在测试通过后生成自动化用例。

端取自 script_generator.determine_script_type 的输出域。开关缺省（无对应行）时
视为关闭（默认不生成，需管理员在系统设置显式开启）。仅管理员可改（routers/system_settings）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationGenSwitch

# 端 → 展示名。顺序即前端展示顺序。
# 仅保留已对接框架生成的端：接口/Web/App（鸿蒙、小程序暂无框架，不提供开关）。
PLATFORM_LABELS: dict[str, str] = {
    "api": "接口",
    "web": "Web",
    "app": "App(移动端)",
}

# 合法的端枚举
VALID_PLATFORMS = tuple(PLATFORM_LABELS.keys())


async def is_generation_enabled(db: AsyncSession, platform: str) -> bool:
    """查询某端的自动化生成开关。无记录默认 False（默认关闭，需显式开启）。"""
    row = (
        await db.execute(
            select(AutomationGenSwitch).where(AutomationGenSwitch.platform == platform)
        )
    ).scalar_one_or_none()
    return False if row is None else row.enabled


async def list_switches(db: AsyncSession) -> list[dict]:
    """返回全部端的开关状态，缺失的端按默认关闭补全（不落库）。"""
    rows = (await db.execute(select(AutomationGenSwitch))).scalars().all()
    by_platform = {r.platform: r for r in rows}
    result: list[dict] = []
    for platform, label in PLATFORM_LABELS.items():
        row = by_platform.get(platform)
        result.append({
            "platform": platform,
            "label": label,
            "enabled": False if row is None else row.enabled,
            "updated_by": row.updated_by if row else None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        })
    return result


async def set_switch(db: AsyncSession, platform: str, enabled: bool, operator: str | None) -> dict:
    """设置某端开关。不存在则创建（upsert）。返回该端最新状态。"""
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"不支持的端: {platform}")
    row = (
        await db.execute(
            select(AutomationGenSwitch).where(AutomationGenSwitch.platform == platform)
        )
    ).scalar_one_or_none()
    if row is None:
        row = AutomationGenSwitch(platform=platform, enabled=enabled, updated_by=operator)
        db.add(row)
    else:
        row.enabled = enabled
        row.updated_by = operator
    await db.commit()
    await db.refresh(row)
    return {
        "platform": row.platform,
        "label": PLATFORM_LABELS.get(row.platform, row.platform),
        "enabled": row.enabled,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
