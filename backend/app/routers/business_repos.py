"""业务仓库登记 CRUD（代码变更分析「下拉选择仓库名」的数据源）。

被测业务仓：worker clone/checkout 后跑静态抽取 + LLM 影响分析。token 为只读 deploy token，
列表接口**不回传 token**（避免泄露），仅登记/编辑时写入。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import BusinessRepo

router = APIRouter(prefix="/api/business-repos", tags=["business-repos"])


class BusinessRepoIn(BaseModel):
    name: str
    git_url: str
    default_branch: str = "master"
    token: str | None = None
    project_id: str | None = None
    clone_depth: int = 50
    enabled: bool = True


def _out(r: BusinessRepo) -> dict:
    return {
        "id": r.id, "name": r.name, "git_url": r.git_url,
        "default_branch": r.default_branch, "project_id": r.project_id,
        "clone_depth": r.clone_depth, "enabled": r.enabled,
        "has_token": bool(r.token),  # 不回传明文 token
        "created_at": r.created_at,
    }


@router.get("")
async def list_repos(project_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """列出可用业务仓（含全局 project_id=null；enabled=True）。"""
    stmt = select(BusinessRepo).where(BusinessRepo.enabled.is_(True))
    if project_id:
        stmt = stmt.where(or_(BusinessRepo.project_id == project_id, BusinessRepo.project_id.is_(None)))
    stmt = stmt.order_by(BusinessRepo.created_at.desc())
    return [_out(r) for r in (await db.execute(stmt)).scalars().all()]


@router.post("", status_code=201)
async def create_repo(body: BusinessRepoIn, db: AsyncSession = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    if not body.name or not body.git_url:
        raise HTTPException(400, "name 与 git_url 必填")
    r = BusinessRepo(
        name=body.name, git_url=body.git_url, default_branch=body.default_branch or "master",
        token=body.token, project_id=body.project_id, clone_depth=body.clone_depth,
        enabled=body.enabled,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _out(r)


@router.delete("/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, db: AsyncSession = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    r = await db.get(BusinessRepo, repo_id)
    if not r:
        raise HTTPException(404, "仓库登记不存在")
    await db.delete(r)
    await db.commit()
