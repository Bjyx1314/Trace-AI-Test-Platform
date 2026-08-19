"""常用登录手机号：按用户(JWT sub)隔离，PC/App 通用。执行弹框输入手机号时可加入/删除/快选。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UserFavPhone

router = APIRouter(prefix="/api/fav-phones", tags=["fav-phones"])


def _sub(user: dict) -> str:
    return str((user or {}).get("sub") or (user or {}).get("email") or "").strip()


def _norm_kind(kind: str | None) -> str:
    return "tenant" if (kind or "").strip() == "tenant" else "phone"


@router.get("")
async def list_fav_phones(kind: str = "phone", db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    """当前用户某类(phone/tenant)常用项，最近加的在前。"""
    sub = _sub(user)
    if not sub:
        return []
    k = _norm_kind(kind)
    rows = (await db.execute(
        select(UserFavPhone).where(UserFavPhone.user_sub == sub, UserFavPhone.kind == k)
        .order_by(UserFavPhone.created_at.desc())
    )).scalars().all()
    return [{"id": r.id, "phone": r.phone} for r in rows]


@router.post("")
async def add_fav_phone(body: dict, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    """加入常用(同值去重)。body: {phone: 值, kind?: phone|tenant}。"""
    sub = _sub(user)
    if not sub:
        raise HTTPException(401, "未识别用户")
    phone = str((body or {}).get("phone") or "").strip()
    k = _norm_kind((body or {}).get("kind"))
    if not phone:
        raise HTTPException(400, "内容不能为空")
    existing = (await db.execute(
        select(UserFavPhone).where(UserFavPhone.user_sub == sub, UserFavPhone.kind == k, UserFavPhone.phone == phone)
    )).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "phone": existing.phone}
    row = UserFavPhone(user_sub=sub, kind=k, phone=phone)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "phone": row.phone}


@router.delete("/{fav_id}")
async def del_fav_phone(fav_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    """删除自己的常用号码(只能删自己的)。"""
    sub = _sub(user)
    row = (await db.execute(
        select(UserFavPhone).where(UserFavPhone.id == fav_id, UserFavPhone.user_sub == sub)
    )).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return {"ok": True}
