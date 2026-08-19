"""认证路由：token 换平台 JWT + 本地账号密码登录 + 当前用户信息。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services.auth import (
    verify_external_task_token, get_or_create_platform_user, create_platform_jwt,
    authenticate_local_user, AuthError,
)
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class VerifyRequest(BaseModel):
    token: str  # external task system token


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """本地账号密码登录（external task system 不可用时使用），返回平台 JWT。"""
    try:
        user = await authenticate_local_user(db, body.username.strip(), body.password)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    jwt = create_platform_jwt(
        user_id=user.username or user.id, role=user.role,
        name=user.name, email=user.email, uid=user.id,
    )
    return {
        "jwt": jwt,
        "user": {
            "id": user.id, "username": user.username, "name": user.name,
            "email": user.email, "role": user.role,
        },
    }


@router.get("/sso-config")
async def sso_config(db: AsyncSession = Depends(get_db)):
    """公开只读：返回 SSO 对接认证地址(external task system 地址),供前端未登录时跳转发券入口。"""
    from app.services.app_settings import resolve_external_task_url
    return {"external_task_url": await resolve_external_task_url(db)}


@router.post("/verify")
async def verify_token(body: VerifyRequest, db: AsyncSession = Depends(get_db)):
    """用 external task system token 换取平台 JWT。前端跳转时调用一次，之后用返回的 jwt 访问其他接口。"""
    from app.services.app_settings import resolve_external_task_url
    base_url = await resolve_external_task_url(db)
    user_info = await verify_external_task_token(body.token, base_url)
    if not user_info:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="external task system token 无效")

    platform_user = await get_or_create_platform_user(
        db,
        external_task_user_id=user_info["user_id"],
        email=user_info.get("email", ""),
        name=user_info.get("name", ""),
        username=user_info.get("username", ""),
        legacy_user_ids=user_info.get("legacy_user_ids") or [],
    )

    if not platform_user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")

    jwt = create_platform_jwt(
        user_id=platform_user.external_task_user_id,
        role=platform_user.role,
        name=platform_user.name,
        email=platform_user.email,
        uid=platform_user.id,
    )

    return {
        "jwt": jwt,
        "user": {
            "id": platform_user.id,
            "external_task_user_id": platform_user.external_task_user_id,
            "name": platform_user.name,
            "email": platform_user.email,
            "role": platform_user.role,
        },
    }


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    """返回当前登录用户信息（用平台 JWT 调用）。"""
    return current_user
