"""代码事实图谱 router（方案 9，阶段五）：扫描建图、查询、影响面扩散、页面种子、陈旧治理。"""
from __future__ import annotations
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.dependencies import get_current_user
from app.config import settings
from app.models import GraphNode, GraphEdge, BusinessRepo
from app.services import code_graph_runner
from app.services.graph import builder, expander, runtime_collector, staleness

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graph", tags=["graph"])


class ScanRequest(BaseModel):
    trigger_mode: str = "local_path"  # local_path / repo_branch
    repo_label: str | None = None
    repo_path: str | None = None
    business_repo_id: str | None = None
    branch: str | None = None
    version: str | None = None  # 扫描版本标记（陈旧治理用），空则用时间无关的自增标记


@router.post("/scan", status_code=202)
async def scan(body: ScanRequest, background_tasks: BackgroundTasks,
               db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if body.trigger_mode == "local_path" and not body.repo_path:
        raise HTTPException(400, "local_path 模式需 repo_path")
    if body.trigger_mode == "repo_branch" and not body.business_repo_id:
        raise HTTPException(400, "repo_branch 模式需 business_repo_id")
    version = body.version or "scan-latest"
    background_tasks.add_task(_run_scan, body.trigger_mode, body.repo_label, body.repo_path,
                              body.business_repo_id, body.branch, version)
    return {"status": "scheduled", "version": version}


async def _run_scan(trigger_mode: str, repo_label: str | None, repo_path: str | None,
                    business_repo_id: str | None, branch: str | None, version: str):
    from app.services.frameworks import repo_manager
    async with AsyncSessionLocal() as db:
        try:
            if trigger_mode == "local_path":
                workspace = Path(repo_path or "")
                label = repo_label or workspace.name
                if not workspace.exists():
                    raise RuntimeError(f"路径不存在：{repo_path}")
            else:
                br = await db.get(BusinessRepo, business_repo_id)
                if not br:
                    raise RuntimeError("业务仓库登记不存在")
                # 复用「拉测试代码」的同一 git 账号：直接用 git_url，凭证依赖执行机已配置的 git 账号
                # （credential helper / SSH / URL 内嵌），与 FrameworkRepo 拉取口径一致，不注入 per-repo token。
                git_url = br.git_url
                dest = repo_manager.workspace_path(settings.business_repo_workspace, br.id, br.name)
                workspace, _ = repo_manager.ensure_repo(git_url, branch or br.default_branch, dest, pull=True)
                label = br.name
            result = await code_graph_runner.run_scan(workspace, label, scan_mode="full")
            if result["status"] != "done":
                logger.warning("图谱扫描失败：%s", result.get("error_message"))
                return
            await builder.import_scan_output(db, result["scan"], version=version)
        except Exception as e:  # noqa: BLE001
            logger.warning("图谱扫描异常：%s", e)


@router.post("/seed-pages", status_code=200)
async def seed_pages(project_id: str | None = None, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """页面缓存 → Page 节点种子（方案 9.10-②，V4 全量前可用）。"""
    n = await runtime_collector.seed_pages_from_cache(db, project_id)
    return {"seeded": n}


@router.get("/nodes")
async def list_nodes(node_type: str | None = None, repo: str | None = None, q: str | None = None,
                     limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)):
    stmt = select(GraphNode).where(GraphNode.status == "active").limit(limit)
    if node_type:
        stmt = stmt.where(GraphNode.node_type == node_type)
    if repo:
        stmt = stmt.where(GraphNode.repo == repo)
    if q:
        stmt = stmt.where(GraphNode.name.ilike(f"%{q}%"))
    rows = (await db.execute(stmt)).scalars().all()
    return [{"node_id": n.node_id, "node_type": n.node_type, "name": n.name, "repo": n.repo,
             "attrs": n.attrs, "status": n.status} for n in rows]


@router.get("/expand")
async def expand_impact(node: str = Query(...), max_hops: int = Query(2, ge=1, le=3), db: AsyncSession = Depends(get_db)):
    """从某节点扩散影响面 + 证据链（方案 9.7）。"""
    return await expander.expand(db, [node], max_hops=max_hops)


@router.get("/stats")
async def graph_stats(db: AsyncSession = Depends(get_db)):
    n = (await db.execute(select(sqlfunc.count()).select_from(GraphNode).where(GraphNode.status == "active"))).scalar_one()
    e = (await db.execute(select(sqlfunc.count()).select_from(GraphEdge).where(GraphEdge.status == "active"))).scalar_one()
    by_type = (await db.execute(
        select(GraphNode.node_type, sqlfunc.count()).where(GraphNode.status == "active").group_by(GraphNode.node_type)
    )).all()
    return {"nodes": n, "edges": e, "by_type": {t: c for t, c in by_type}}


class StaleRequest(BaseModel):
    current_version: str
    previous_versions: list[str] = []


@router.post("/maintenance/staleness")
async def run_staleness(body: StaleRequest, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return await staleness.mark_stale(db, body.current_version, body.previous_versions)
