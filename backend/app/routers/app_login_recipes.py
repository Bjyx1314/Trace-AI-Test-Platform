"""App 自动登录配方 CRUD（配置驱动地把各 App 的登录放进平台，无需改代码）。

各 App 登录方式全端固定：手机号 + 固定验证码。配方只描述差异点：
- match_keywords：逗号分隔关键词，对执行端 "{platform_key} {label}" 小写做「全部命中」匹配。
- env_steps：选环境的自然语言步骤（一行一步，可用 {env} 占位）；留空=无需选环境。
- restart_after_env：选完环境是否杀 App 重启。needs_tenant：是否需登录后选/切租户。
启动页/引导页/权限弹窗由执行器通用前置目标自动趟过，配方里不用写。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import AppLoginRecipe

router = APIRouter(prefix="/api/app-login-recipes", tags=["app-login-recipes"])


class RecipeIn(BaseModel):
    name: str
    match_keywords: str
    env_steps: str | None = None
    restart_after_env: bool = False
    needs_tenant: bool = False
    enabled: bool = True


def _out(r: AppLoginRecipe) -> dict:
    return {
        "id": r.id, "name": r.name, "match_keywords": r.match_keywords,
        "env_steps": r.env_steps, "restart_after_env": r.restart_after_env,
        "needs_tenant": r.needs_tenant, "enabled": r.enabled,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


@router.get("")
async def list_recipes(db: AsyncSession = Depends(get_db)):
    """列出全部配方（含停用），管理页用。"""
    stmt = select(AppLoginRecipe).order_by(AppLoginRecipe.created_at.desc())
    return [_out(r) for r in (await db.execute(stmt)).scalars().all()]


@router.post("", status_code=201)
async def create_recipe(body: RecipeIn, db: AsyncSession = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    if not body.name or not body.match_keywords:
        raise HTTPException(400, "name 与 match_keywords 必填")
    r = AppLoginRecipe(
        name=body.name, match_keywords=body.match_keywords,
        env_steps=body.env_steps, restart_after_env=body.restart_after_env,
        needs_tenant=body.needs_tenant, enabled=body.enabled,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _out(r)


@router.put("/{recipe_id}")
async def update_recipe(recipe_id: str, body: RecipeIn, db: AsyncSession = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    r = await db.get(AppLoginRecipe, recipe_id)
    if not r:
        raise HTTPException(404, "配方不存在")
    r.name = body.name
    r.match_keywords = body.match_keywords
    r.env_steps = body.env_steps
    r.restart_after_env = body.restart_after_env
    r.needs_tenant = body.needs_tenant
    r.enabled = body.enabled
    await db.commit()
    await db.refresh(r)
    return _out(r)


@router.delete("/{recipe_id}", status_code=204)
async def delete_recipe(recipe_id: str, db: AsyncSession = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    r = await db.get(AppLoginRecipe, recipe_id)
    if not r:
        raise HTTPException(404, "配方不存在")
    await db.delete(r)
    await db.commit()
