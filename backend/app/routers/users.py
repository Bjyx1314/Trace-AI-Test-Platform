"""用户管理路由（仅管理员）：列出用户、修改角色、新增本地账号、禁用/启用。"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import PlatformUser
from app.dependencies import require_admin
from app.services.password import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


class RoleUpdate(BaseModel):
    role: str  # admin / user


class CreateUser(BaseModel):
    username: str
    password: str
    name: str | None = None
    role: str = "user"  # admin / user


class ActiveUpdate(BaseModel):
    is_active: bool


class AiKeyUpdate(BaseModel):
    ai_api_key: str | None = None  # 该用户专属中转 key；传空/None 表示清除


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    k = key.strip()
    return (k[:6] + "..." + k[-4:]) if len(k) > 12 else "***"


def _user_dict(u: PlatformUser) -> dict:
    return {
        "id": u.id,
        "external_user_id": u.external_user_id,
        "username": u.username,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "auth_source": u.auth_source,
        "has_ai_key": bool((u.ai_api_key or "").strip()),
        "ai_key_masked": _mask(u.ai_api_key),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    """列出所有平台用户（管理员专用）。"""
    result = await db.execute(select(PlatformUser).order_by(PlatformUser.created_at))
    return [_user_dict(u) for u in result.scalars().all()]


@router.post("", status_code=201)
async def create_user(
    body: CreateUser,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """新增本地账号（账号+密码），管理员专用。"""
    username = body.username.strip()
    if not username:
        raise HTTPException(400, "账号不能为空")
    if len(body.password) < 6:
        raise HTTPException(400, "密码长度至少 6 位")
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "角色只能是 admin 或 user")
    exists = (await db.execute(
        select(PlatformUser).where(PlatformUser.username == username)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "该账号已存在")

    user = PlatformUser(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(body.password),
        name=body.name or username,
        role=body.role,
        is_active=True,
        auth_source="local",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.patch("/{user_id}/active")
async def set_user_active(
    user_id: str,
    body: ActiveUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(require_admin),
):
    """禁用 / 启用账号（管理员专用）。"""
    user = await db.get(PlatformUser, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    if not body.is_active and user.id == current_admin.get("uid"):
        raise HTTPException(400, "不能禁用自己的账号")
    user.is_active = body.is_active
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.patch("/{user_id}/ai-key")
async def set_user_ai_key(
    user_id: str,
    body: AiKeyUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """为用户配置专属 AI 中转 key（管理员专用）。传空表示清除。"""
    user = await db.get(PlatformUser, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    key = (body.ai_api_key or "").strip()
    user.ai_api_key = key or None
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.patch("/{user_id}/role")
async def update_user_role(
    user_id: str,
    body: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(require_admin),
):
    """修改用户角色（管理员专用）。"""
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "角色只能是 admin 或 user")

    user = await db.get(PlatformUser, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")

    # 不能降级自己
    if user.id == current_admin.get("uid") and body.role != "admin":
        raise HTTPException(400, "不能降级自己的权限")

    user.role = body.role
    await db.commit()
    await db.refresh(user)
    return {"id": user.id, "name": user.name, "role": user.role}
