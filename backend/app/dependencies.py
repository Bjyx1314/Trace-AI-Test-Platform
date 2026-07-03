"""FastAPI 依赖注入：当前用户认证。"""
from fastapi import Depends, HTTPException, Header
from typing import Optional
from app.config import settings
from app.services.auth import verify_platform_jwt

# MOCK/本地开发态的兜底管理员（无外部 SSO 时使用，仅 settings.mock_mode 生效）
_MOCK_ADMIN = {"sub": "mock-admin", "role": "admin", "name": "Mock管理员", "email": "mock@local"}


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """从 Authorization: Bearer <token> 中解析平台 JWT，返回用户信息。

    有合法 token 一律以 token 为准。无/失效 token 时：mock 模式放行为 mock 管理员
    （与前端「mock 不要求登录」一致，避免本地无 SSO 时管理员页 401）；生产抛 401。
    """
    if authorization and authorization.startswith("Bearer "):
        payload = verify_platform_jwt(authorization[7:])
        if payload:
            return payload
        if not settings.mock_mode:
            raise HTTPException(status_code=401, detail="Token 无效或已过期")

    if settings.mock_mode:
        return dict(_MOCK_ADMIN)
    raise HTTPException(status_code=401, detail="未登录，请使用本地账号或已配置的外部 SSO")


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """要求管理员角色。"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user
