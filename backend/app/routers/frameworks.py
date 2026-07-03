"""框架仓库登记 + 索引 + 用例生成/review/提交 的 HTTP 入口。

服务层在 app.services.frameworks.*，本路由只做请求编排与序列化。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import FrameworkRepo, TestCase
from app.services.frameworks import indexer
from app.services.frameworks import repos as repo_svc

router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


# ── Schemas ────────────────────────────────────────────────────────────────

class FrameworkRepoCreate(BaseModel):
    name: str
    repo_type: str  # interface/web/app
    git_url: str
    branch: str = "main"
    project_id: str | None = None
    description: str | None = None
    local_path: str | None = None
    tests_root: str | None = None
    data_root: str | None = None
    keyword_root: str | None = None
    run_command: str | None = None
    install_command: str | None = None
    env_json: dict | None = None


class FrameworkRepoUpdate(BaseModel):
    name: str | None = None
    branch: str | None = None
    description: str | None = None
    local_path: str | None = None
    tests_root: str | None = None
    data_root: str | None = None
    keyword_root: str | None = None
    run_command: str | None = None
    install_command: str | None = None
    env_json: dict | None = None
    enabled: bool | None = None


class ReindexRequest(BaseModel):
    sync_git: bool = True  # False=直接扫 local_path（仓库已在本地）


class CommitRequest(BaseModel):
    push: bool = False


def _serialize(r: FrameworkRepo, *, with_index: bool = False) -> dict:
    idx = r.index_json or {}
    summary = {}
    if r.repo_type == "interface":
        summary = {"class_count": idx.get("class_count"), "keyword_count": idx.get("keyword_count")}
    else:
        summary = {"page_count": idx.get("page_count"), "flow_count": idx.get("flow_count"),
                   "fixture_count": len(idx.get("fixtures", []) or [])}
    data = {
        "id": r.id, "name": r.name, "repo_type": r.repo_type, "project_id": r.project_id,
        "description": r.description, "git_url": r.git_url, "branch": r.branch,
        "local_path": r.local_path, "tests_root": r.tests_root, "data_root": r.data_root,
        "keyword_root": r.keyword_root, "run_command": r.run_command,
        "install_command": r.install_command, "env_json": r.env_json,
        "index_status": r.index_status, "index_commit": r.index_commit,
        "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
        "index_summary": summary, "enabled": r.enabled,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if with_index:
        data["index_json"] = r.index_json
    return data


# ── 仓库 CRUD ──────────────────────────────────────────────────────────────

@router.get("")
async def list_repos(project_id: str | None = None, repo_type: str | None = None,
                     db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    stmt = select(FrameworkRepo)
    if project_id:
        stmt = stmt.where(FrameworkRepo.project_id == project_id)
    if repo_type:
        stmt = stmt.where(FrameworkRepo.repo_type == repo_type)
    rows = (await db.execute(stmt.order_by(FrameworkRepo.created_at.desc()))).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_repo(body: FrameworkRepoCreate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    if body.repo_type not in ("interface", "web", "app"):
        raise HTTPException(400, "repo_type 必须是 interface/web/app")
    repo = FrameworkRepo(**body.model_dump())
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return _serialize(repo)


@router.get("/{repo_id}")
async def get_repo(repo_id: str, with_index: bool = False, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    repo = await db.get(FrameworkRepo, repo_id)
    if repo is None:
        raise HTTPException(404, "框架仓库不存在")
    return _serialize(repo, with_index=with_index)


@router.patch("/{repo_id}")
async def update_repo(repo_id: str, body: FrameworkRepoUpdate, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    repo = await db.get(FrameworkRepo, repo_id)
    if repo is None:
        raise HTTPException(404, "框架仓库不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(repo, k, v)
    await db.commit()
    await db.refresh(repo)
    return _serialize(repo)


@router.delete("/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    repo = await db.get(FrameworkRepo, repo_id)
    if repo is None:
        raise HTTPException(404, "框架仓库不存在")
    await db.delete(repo)
    await db.commit()


# ── 索引 ──────────────────────────────────────────────────────────────────

@router.post("/{repo_id}/reindex")
async def reindex_repo(repo_id: str, body: ReindexRequest | None = None,
                       db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    body = body or ReindexRequest()
    try:
        repo = await indexer.reindex(db, repo_id, sync_git=body.sync_git)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"索引失败: {exc}")
    return _serialize(repo, with_index=True)


# ── 用例生成 / review / 提交 ─────────────────────────────────────────────────

@router.post("/cases/{case_id}/generate")
async def generate_case(case_id: str, db: AsyncSession = Depends(get_db)):
    try:
        tc = await repo_svc.generate_and_store(db, case_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"case_id": tc.id, "script_path": tc.script_path,
            "framework_repo_id": tc.framework_repo_id,
            "generated_artifacts": tc.generated_artifacts}


@router.post("/cases/{case_id}/review")
async def review_case(case_id: str, db: AsyncSession = Depends(get_db)):
    try:
        rr = await repo_svc.review_generated(db, case_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": rr.ok, "issues": rr.issues, "warnings": rr.warnings}


@router.post("/cases/{case_id}/commit")
async def commit_case(case_id: str, body: CommitRequest | None = None,
                      db: AsyncSession = Depends(get_db)):
    body = body or CommitRequest()
    try:
        result = await repo_svc.commit_generated(db, case_id, push=body.push)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 —— git 错误
        raise HTTPException(500, f"提交失败: {exc}")
    return result
