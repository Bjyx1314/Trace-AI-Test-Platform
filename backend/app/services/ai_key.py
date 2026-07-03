"""按发起用户解析 AI key（per-user key）。

所有 AI 操作（需求分析/用例生成/执行/App）都走发起人自己的中转 key：
- 入口处用 resolve_user_ai_key 取发起人 key（未配置→ NoAiKeyError，上层转成清晰报错）；
- 用 app.agents.llm.set_current_ai_key 把它设进当前上下文（请求或后台任务），AI 调用即走该 key。
key 都是同一中转（base_url 不变），仅 key 不同。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformUser


class NoAiKeyError(Exception):
    """发起用户未分配 AI key。message 可直接展示。"""
    def __init__(self, who: str):
        self.who = who
        super().__init__(f"您（{who}）还未分配 AI key，请联系管理员在「用户管理」中为您配置后再操作")


async def get_user_record(db: AsyncSession, user: dict | None) -> PlatformUser | None:
    """按 JWT 载荷找到 PlatformUser：优先 uid(主键)，回退 sub(external_user_id)。"""
    user = user or {}
    uid = user.get("uid")
    sub = user.get("sub")
    u = None
    if uid:
        u = await db.get(PlatformUser, uid)
    if u is None and sub:
        u = (await db.execute(
            select(PlatformUser).where(PlatformUser.external_user_id == sub)
        )).scalar_one_or_none()
    return u


async def resolve_user_ai_key(db: AsyncSession, user: dict | None) -> str:
    """返回发起用户的 AI key。

    顺序：① 用户自己的 key；② 管理员回退系统设置的默认 key（admin 用默认 key，无需单独分配）；
    ③ 普通用户无 key → NoAiKeyError。模型/provider/base_url 始终用系统设置的全局配置，仅 key 按人。
    """
    u = await get_user_record(db, user)
    if u and (u.ai_api_key or "").strip():
        return u.ai_api_key.strip()
    # 内置 admin 账号回退系统设置的默认 key（仅这个账号，真人管理员仍需自己的 key）
    from app.config import settings
    if u and u.username and u.username == settings.default_admin_username:
        gk = (settings.ai_api_key or settings.anthropic_api_key or "").strip()
        if gk:
            return gk
    who = (user or {}).get("name") or (user or {}).get("sub") or "未知用户"
    raise NoAiKeyError(who)


async def apply_user_ai_key_soft(db: AsyncSession, user: dict | None) -> None:
    """软设置：发起人有 key 就设进当前上下文，没有则不动(回退全局)，绝不报错。
    用于次要/同步 AI 调用(覆盖分析、缺陷诊断等)，过渡期不阻断。"""
    from app.agents.llm import set_current_ai_key
    try:
        u = await get_user_record(db, user)
        if u and (u.ai_api_key or "").strip():
            set_current_ai_key(u.ai_api_key.strip())
    except Exception:
        pass
