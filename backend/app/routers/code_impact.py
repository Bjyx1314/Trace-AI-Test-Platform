"""代码影响分析 router（手动触发，platform mode）。方案 8。

无 GitLab webhook：三种手动触发 trigger_mode ∈ paste_diff / local_path / repo_branch。
先落 ChangeImpactRecord(pending) 幂等，后台跑 skill，失败不阻塞。
分析成功后若关联需求，触发阶段 B 增量（缺口用例 + 已有用例标需回归）。
"""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.dependencies import get_current_user
from app.models import ChangeImpactRecord, BusinessRepo
from app.config import settings
from app.services import code_impact_runner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/code-impact", tags=["code-impact"])


class AnalyzeRequest(BaseModel):
    trigger_mode: str = "repo_branch"  # repo_branch / local_path（paste_diff 入口已下线，仅保留历史记录兼容）
    requirement_id: str | None = None
    # paste_diff
    diff_text: str | None = None
    repo_label: str | None = None
    # local_path
    repo_path: str | None = None
    # repo_branch（人工拉取：下拉选仓库 + 目标分支 + commit id，基准固定 master）
    business_repo_id: str | None = None
    base_branch: str | None = None      # 缺省 master
    target_branch: str | None = None    # 目标分支（commit 所在分支）
    commit_id: str | None = None        # 目标 commit（填则 checkout 到该 commit 与 master 比对）
    mr_id: str | None = None


def _out(rec: ChangeImpactRecord) -> dict:
    return {
        "impact_id": rec.id,
        "requirement_id": rec.requirement_id,
        "business_repo_id": rec.business_repo_id,
        "trigger_mode": rec.trigger_mode,
        "mr_id": rec.mr_id,
        "repo_label": rec.repo_label,
        "base_branch": rec.base_branch,
        "target_branch": rec.target_branch,
        "head_sha": rec.head_sha,
        "status": rec.status,
        "analysis_status": rec.status,  # 前端契约别名
        "schema_version": rec.schema_version,
        "impact_json": rec.impact_json,
        "error_message": rec.error_message,
        "created_at": rec.created_at,
        "finished_at": rec.finished_at,
        **(rec.impact_json or {}),  # 展开 change_summary/impact_scope/... 便于前端直接读
    }


@router.post("/analyze", status_code=202)
async def analyze(body: AnalyzeRequest, background_tasks: BackgroundTasks,
                  db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if body.trigger_mode == "paste_diff" and not body.diff_text:
        raise HTTPException(400, "paste_diff 模式必须提供 diff_text")
    if body.trigger_mode == "local_path" and not body.repo_path:
        raise HTTPException(400, "local_path 模式必须提供 repo_path")
    if body.trigger_mode == "repo_branch" and not body.business_repo_id:
        raise HTTPException(400, "repo_branch 模式必须提供 business_repo_id")
    if body.trigger_mode == "repo_branch" and not (body.target_branch or body.commit_id):
        raise HTTPException(400, "repo_branch 模式必须提供 目标分支 或 commit id")

    base_branch = body.base_branch or "master"  # 基准固定 master（req 1）
    rec = ChangeImpactRecord(
        requirement_id=body.requirement_id,
        business_repo_id=body.business_repo_id,
        trigger_mode=body.trigger_mode,
        mr_id=body.mr_id or body.commit_id,
        repo_label=body.repo_label or body.repo_path,
        base_branch=base_branch,
        target_branch=body.target_branch,
        status="pending",
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)

    # 解析发起人 AI key(供 LLM 分析用；无 key 不阻塞——存量依赖面仍出、LLM 部分降级)
    try:
        from app.services.ai_key import resolve_user_ai_key
        ai_key = await resolve_user_ai_key(db, current_user)
    except Exception:
        ai_key = None

    background_tasks.add_task(
        _run_impact, rec.id, body.trigger_mode, body.diff_text,
        body.repo_path, body.business_repo_id, base_branch, body.target_branch, body.commit_id, ai_key,
    )
    return _out(rec)


@router.get("")
async def list_records(requirement_id: str | None = None, limit: int = Query(50, ge=1, le=200),
                       db: AsyncSession = Depends(get_db)):
    stmt = select(ChangeImpactRecord).order_by(ChangeImpactRecord.created_at.desc()).limit(limit)
    if requirement_id:
        stmt = stmt.where(ChangeImpactRecord.requirement_id == requirement_id)
    recs = (await db.execute(stmt)).scalars().all()
    return [_out(r) for r in recs]


@router.get("/{impact_id}")
async def get_record(impact_id: str, db: AsyncSession = Depends(get_db)):
    rec = await db.get(ChangeImpactRecord, impact_id)
    if not rec:
        raise HTTPException(404, "记录不存在")
    return _out(rec)


@router.get("/{impact_id}/report.md")
async def get_report_md(impact_id: str, db: AsyncSession = Depends(get_db)):
    rec = await db.get(ChangeImpactRecord, impact_id)
    if not rec:
        raise HTTPException(404, "记录不存在")
    return Response(content=rec.impact_md or "（本次无影响分析报告）", media_type="text/markdown; charset=utf-8")


async def _find_cache(db: AsyncSession, *, business_repo_id: str | None, base: str | None,
                      head_sha: str | None, exclude_id: str) -> ChangeImpactRecord | None:
    """相同 commit 分析结果缓存（req 6）：同仓库 + 同 base + 同 head_sha 的已完成记录直接复用，省 token。"""
    if not (business_repo_id and head_sha):
        return None
    stmt = (
        select(ChangeImpactRecord)
        .where(ChangeImpactRecord.business_repo_id == business_repo_id,
               ChangeImpactRecord.base_branch == base,
               ChangeImpactRecord.head_sha == head_sha,
               ChangeImpactRecord.status.in_(["done", "degraded"]),
               ChangeImpactRecord.id != exclude_id,
               ChangeImpactRecord.impact_json.isnot(None))
        .order_by(ChangeImpactRecord.finished_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


# ── 后台执行 ────────────────────────────────────────────────────────────────
async def _run_impact(record_id: str, trigger_mode: str, diff_text: str | None,
                      repo_path: str | None, business_repo_id: str | None,
                      base_branch: str | None, target_branch: str | None,
                      commit_id: str | None = None, ai_key: str | None = None):
    """后台：静态抽取变更 →（命中缓存则复用）→ LLM 只吃摘要跑分析 → 落库 → 阶段 B 增量。失败不阻塞。"""
    import hashlib
    from app.services.frameworks import repo_manager
    from app.services import code_change_static
    from app.agents.llm import set_current_ai_key
    set_current_ai_key(ai_key)  # LLM 走发起人自己的 key（无 key 时分析降级，存量依赖面仍出）

    async with AsyncSessionLocal() as db:
        rec = await db.get(ChangeImpactRecord, record_id)
        if not rec:
            return
        rec.status = "running"
        await db.commit()

        req_context: str | None = None
        if rec.requirement_id:
            from app.models import Requirement
            req = await db.get(Requirement, rec.requirement_id)
            if req:
                req_context = (req.title or "")[:500]

        head_sha: str | None = None
        try:
            # 1) 静态抽取变更集（不调 LLM）
            if trigger_mode == "paste_diff":
                summary = code_change_static.extract_from_diff(diff_text or "")
                head_sha = "diff:" + hashlib.sha256((diff_text or "").encode("utf-8")).hexdigest()[:16]
                summary.head_sha = head_sha
            elif trigger_mode == "local_path":
                workspace = Path(repo_path or "")
                if not workspace.exists():
                    raise RuntimeError(f"本地路径不存在：{repo_path}")
                summary = code_change_static.extract_from_git(workspace, base_branch or "master", None)
                head_sha = summary.head_sha
            elif trigger_mode == "repo_branch":
                br = await db.get(BusinessRepo, business_repo_id)
                if not br:
                    raise RuntimeError("业务仓库登记不存在")
                # 拉分支复用「拉测试代码」的同一 git 账号：直接用 git_url，凭证依赖执行机已配置
                # 的 git 账号（credential helper / SSH / URL 内嵌），与 FrameworkRepo 拉取口径一致，
                # 不再单独注入 per-repo deploy token。
                git_url = br.git_url
                branch = target_branch or br.default_branch
                dest = repo_manager.workspace_path(settings.business_repo_workspace, br.id, br.name)
                workspace, branch_sha = repo_manager.ensure_repo(git_url, branch, dest, pull=True)
                repo_manager.fetch_branch(dest, base_branch or "master")  # 保证 origin/master 可比对
                if commit_id:
                    head_sha = repo_manager.checkout_commit(dest, commit_id)
                else:
                    head_sha = branch_sha
                rec.repo_label = br.name
                rec.head_sha = head_sha
                # 2) 缓存命中则复用（req 6）：按记录存储口径的 base_branch（"master"）匹配
                cached = await _find_cache(db, business_repo_id=business_repo_id,
                                           base=base_branch or "master",
                                           head_sha=head_sha, exclude_id=record_id)
                if cached:
                    rec.status = cached.status
                    rec.schema_version = cached.schema_version
                    rec.impact_json = {**(cached.impact_json or {}), "_cached_from": cached.id}
                    rec.impact_md = cached.impact_md
                    rec.finished_at = datetime.now()
                    await db.commit()
                    logger.info("代码影响分析命中缓存 record=%s ← %s", record_id, cached.id)
                    summary = None  # 跳过 LLM
                else:
                    summary = code_change_static.extract_from_git(
                        workspace, f"origin/{base_branch or 'master'}", head_sha)
            else:
                raise RuntimeError(f"未知 trigger_mode: {trigger_mode}")

            # 3) LLM 只吃结构化摘要 + 受限 diff（未命中缓存时）
            if summary is not None:
                result = await code_impact_runner.run_analysis(
                    summary, mr_id=rec.mr_id, requirement_context=req_context)
                rec.status = result["status"]
                rec.schema_version = result.get("schema_version")
                rec.impact_json = result.get("impact_json")
                rec.impact_md = result.get("impact_md")
                rec.error_message = result.get("error_message")
                rec.head_sha = head_sha
                rec.finished_at = datetime.now()
                await db.commit()
        except Exception as e:  # noqa: BLE001 失败不阻塞
            logger.warning("代码影响分析失败 record=%s: %s", record_id, e)
            rec.status = "failed"
            rec.error_message = str(e)[:500]
            rec.finished_at = datetime.now()
            await db.commit()

    # 阶段 B 增量：分析可用且关联需求时，缺口增量新增用例 + 已有用例标记
    if rec.status in ("done", "degraded") and rec.requirement_id:
        try:
            from app.routers.pipeline import run_impact_incremental
            await run_impact_incremental(record_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("阶段B增量失败 record=%s: %s", record_id, e)
