"""按登录用户限定数据可见范围：普通用户只看自己创建/归属的数据，管理员不受限。

需求/质量看板等以 owner_name(归属人姓名) 为口径；本模块统一计算「应生效的归属过滤值」，
避免普通用户通过直接调接口或传参越权查看他人数据。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


def is_admin(current_user: dict | None) -> bool:
    return (current_user or {}).get("role") == "admin"


async def admin_user_ids(db: AsyncSession) -> set[str]:
    """所有管理员的用户标识集合(PlatformUser.id + external_user_id)。

    用于把「管理员账号关联的真机」识别为公共 App 测试设备：设备上报的 owner_user_id
    可能是平台主键(uid)或外部 SSO 用户 id(sub)，故两者都收进集合以便命中。
    """
    from sqlalchemy import select
    from app.models import PlatformUser
    ids: set[str] = set()
    rows = (await db.execute(
        select(PlatformUser).where(PlatformUser.role == "admin")
    )).scalars().all()
    for u in rows:
        if u.id:
            ids.add(str(u.id))
        external_id = getattr(u, "external_user_id", None)
        if external_id:
            ids.add(str(external_id))
    return ids


async def current_owner_name(db: AsyncSession, current_user: dict | None) -> str | None:
    """当前登录人的归属姓名(与 Requirement.owner_name 同口径)。

    优先平台用户记录的姓名，回退 token 里的 name。
    """
    try:
        from app.services.ai_key import get_user_record
        u = await get_user_record(db, current_user)
        if u and u.name:
            return u.name
    except Exception:
        pass
    return (current_user or {}).get("name")


async def enforce_owner(db: AsyncSession, current_user: dict | None,
                        requested_owner: str | None) -> str | None:
    """返回应实际生效的归属过滤值：

    - 管理员：返回 requested_owner(None=全部 / 指定人筛选)，不强制。
    - 普通用户：强制本人姓名，忽略前端传入，防越权看他人数据。
    """
    if is_admin(current_user):
        return requested_owner
    return await current_owner_name(db, current_user)
