"""页面结构缓存 CRUD（设计文档7.3节）。

存储URL模式对应的DOM区域结构，供AI测试执行时注入上下文。
MOCK_MODE下支持手动录入管理；真实模式下由Playwright扫描自动更新。
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import PageStructureCache, PageCacheDiff, Project
from app.schemas import PageRegion
from app.services.page_cache_service import (
    normalize_url,
    compute_region_hashes,
    match_cache,
    is_stale,
    STALE_AFTER_DAYS,
)
from app.services.page_recorder import record_pages, playwright_cli, RecorderError

router = APIRouter(prefix="/api/page-cache", tags=["page-cache"])


class PageCacheCreate(BaseModel):
    project_id: str
    url_pattern: str
    page_name: str
    dom_hash: dict | None = None
    regions: list | None = None


class PageCacheUpdate(BaseModel):
    url_pattern: str | None = None
    page_name: str | None = None
    dom_hash: dict | None = None
    regions: list | None = None
    status: str | None = None


class PageCacheMatchRequest(BaseModel):
    """执行引擎上报：当前访问的 URL + 实时探索到的区块结构。"""
    project_id: str
    url: str
    regions: list[PageRegion]


class PageCacheUpsertRequest(BaseModel):
    """执行中自动补充缓存（7.3.1 自动补充 / 7.3.6 新页面自动写入）。"""
    project_id: str
    url: str
    page_name: str
    regions: list[PageRegion]


class PageCacheDiffReport(BaseModel):
    """执行中发现区块 hash 不一致，上报到差异队列（7.3.5），不立即改缓存。"""
    project_id: str
    url: str
    page_name: str
    regions: list[PageRegion]
    changed_regions: list[str] = []
    cache_id: str | None = None


class DiffResolveRequest(BaseModel):
    resolved_by: str = "系统"


@router.get("")
async def list_page_caches(project_id: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(PageStructureCache).order_by(PageStructureCache.updated_at.desc())
    if project_id:
        q = q.where(PageStructureCache.project_id == project_id)
    caches = (await db.execute(q)).scalars().all()
    return [_to_dict(c) for c in caches]


@router.post("", status_code=201)
async def create_page_cache(body: PageCacheCreate, db: AsyncSession = Depends(get_db)):
    proj = await db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    entry = PageStructureCache(**body.model_dump())
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _to_dict(entry)


@router.put("/{cache_id}")
async def update_page_cache(cache_id: str, body: PageCacheUpdate, db: AsyncSession = Depends(get_db)):
    entry = await db.get(PageStructureCache, cache_id)
    if not entry:
        raise HTTPException(404, "Cache entry not found")
    for k, v in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(entry, k, v)
    entry.updated_at = datetime.now()
    await db.commit()
    await db.refresh(entry)
    return _to_dict(entry)


@router.delete("/{cache_id}", status_code=204)
async def delete_page_cache(cache_id: str, db: AsyncSession = Depends(get_db)):
    entry = await db.get(PageStructureCache, cache_id)
    if not entry:
        raise HTTPException(404, "Cache entry not found")
    await db.delete(entry)
    await db.commit()


@router.post("/{cache_id}/invalidate")
async def invalidate_page_cache(cache_id: str, db: AsyncSession = Depends(get_db)):
    """将缓存条目标记为stale，触发下次执行时重新扫描（7.3.6 手动重扫描）。"""
    entry = await db.get(PageStructureCache, cache_id)
    if not entry:
        raise HTTPException(404, "Cache entry not found")
    entry.status = "stale"
    entry.updated_at = datetime.now()
    await db.commit()
    return {"status": "stale", "id": cache_id}


# ── 7.3 核心逻辑端点 ────────────────────────────────────────────────────────

async def _find_cache_by_url(db: AsyncSession, project_id: str, url: str) -> PageStructureCache | None:
    """按归一化 pattern 在项目内查找命中的缓存条目。"""
    pattern = normalize_url(url)
    return (await db.execute(
        select(PageStructureCache).where(
            PageStructureCache.project_id == project_id,
            PageStructureCache.url_pattern == pattern,
        )
    )).scalars().first()


@router.post("/match")
async def match_page_cache(body: PageCacheMatchRequest, db: AsyncSession = Depends(get_db)):
    """执行时缓存命中决策（7.3.3）。

    入参为执行引擎实时探索到的区块结构；按归一化 URL pattern 找缓存，
    逐区块对比 hash，返回 full_hit / partial_hit / no_cache 及命中/未命中区块，
    供执行引擎决定是直接用缓存、局部探索还是完整探索。命中时累加命中计数。
    """
    pattern = normalize_url(body.url)
    current_hash = compute_region_hashes([r.model_dump() for r in body.regions])
    entry = await _find_cache_by_url(db, body.project_id, body.url)

    cached_hash = entry.dom_hash if (entry and entry.status == "active") else None
    decision = match_cache(cached_hash, current_hash)

    if entry and decision["result"] in ("full_hit", "partial_hit"):
        entry.hit_count += 1
        entry.last_hit_at = datetime.now()
        await db.commit()

    return {
        "url_pattern": pattern,
        "cache_id": entry.id if entry else None,
        "current_hash": current_hash,
        **decision,
    }


@router.post("/upsert")
async def upsert_page_cache(body: PageCacheUpsertRequest, db: AsyncSession = Depends(get_db)):
    """执行中自动补充缓存（7.3.1 自动补充 / 7.3.6 新页面自动写入）。

    按归一化 pattern：无则新建（新页面无需人工），有则刷新结构与 hash。
    用于执行引擎访问到缓存库未记录的页面时直接写入共享缓存。
    """
    proj = await db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    pattern = normalize_url(body.url)
    regions = [r.model_dump() for r in body.regions]
    dom_hash = compute_region_hashes(regions)
    entry = await _find_cache_by_url(db, body.project_id, body.url)

    created = False
    if entry is None:
        entry = PageStructureCache(
            project_id=body.project_id,
            url_pattern=pattern,
            page_name=body.page_name,
            dom_hash=dom_hash,
            regions=regions,
            status="active",
        )
        db.add(entry)
        created = True
    else:
        entry.page_name = body.page_name
        entry.dom_hash = dom_hash
        entry.regions = regions
        entry.status = "active"
        entry.updated_at = datetime.now()

    await db.commit()
    await db.refresh(entry)
    return {"created": created, **_to_dict(entry)}


@router.post("/diffs", status_code=201)
async def report_cache_diff(body: PageCacheDiffReport, db: AsyncSession = Depends(get_db)):
    """上报缓存差异到提醒队列（7.3.5）。执行中发现 hash 不一致时调用，不立即改缓存。"""
    proj = await db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    pattern = normalize_url(body.url)
    regions = [r.model_dump() for r in body.regions]
    diff = PageCacheDiff(
        project_id=body.project_id,
        cache_id=body.cache_id,
        url_pattern=pattern,
        page_name=body.page_name,
        changed_regions=body.changed_regions,
        new_regions=regions,
        new_dom_hash=compute_region_hashes(regions),
        status="pending",
    )
    db.add(diff)
    await db.commit()
    await db.refresh(diff)
    return _diff_to_dict(diff)


@router.get("/diffs")
async def list_cache_diffs(
    project_id: str | None = None,
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
):
    """差异提醒列表（7.3.5）。默认只看待处理的差异。"""
    q = select(PageCacheDiff).order_by(PageCacheDiff.created_at.desc())
    if project_id:
        q = q.where(PageCacheDiff.project_id == project_id)
    if status and status != "all":
        q = q.where(PageCacheDiff.status == status)
    diffs = (await db.execute(q)).scalars().all()
    return [_diff_to_dict(d) for d in diffs]


@router.post("/diffs/{diff_id}/confirm")
async def confirm_cache_diff(diff_id: str, body: DiffResolveRequest, db: AsyncSession = Depends(get_db)):
    """确认差异（7.3.5）：把新结构写回共享缓存，记录操作人/时间，缓存全平台生效。"""
    diff = await db.get(PageCacheDiff, diff_id)
    if not diff:
        raise HTTPException(404, "Diff not found")
    if diff.status != "pending":
        raise HTTPException(409, f"Diff already {diff.status}")

    # 写回共享缓存：有 cache_id 则更新，否则按 pattern upsert
    entry = await db.get(PageStructureCache, diff.cache_id) if diff.cache_id else None
    if entry is None:
        entry = (await db.execute(
            select(PageStructureCache).where(
                PageStructureCache.project_id == diff.project_id,
                PageStructureCache.url_pattern == diff.url_pattern,
            )
        )).scalars().first()

    if entry is None:
        entry = PageStructureCache(
            project_id=diff.project_id,
            url_pattern=diff.url_pattern,
            page_name=diff.page_name,
            dom_hash=diff.new_dom_hash,
            regions=diff.new_regions,
            status="active",
        )
        db.add(entry)
    else:
        entry.page_name = diff.page_name
        entry.dom_hash = diff.new_dom_hash
        entry.regions = diff.new_regions
        entry.status = "active"
        entry.updated_at = datetime.now()

    diff.status = "confirmed"
    diff.resolved_by = body.resolved_by
    diff.resolved_at = datetime.now()
    await db.commit()
    await db.refresh(entry)
    return {"diff_id": diff_id, "status": "confirmed", "cache": _to_dict(entry)}


@router.post("/diffs/{diff_id}/dismiss")
async def dismiss_cache_diff(diff_id: str, body: DiffResolveRequest, db: AsyncSession = Depends(get_db)):
    """暂不更新（7.3.5）：保留旧缓存，差异标记为已忽略。"""
    diff = await db.get(PageCacheDiff, diff_id)
    if not diff:
        raise HTTPException(404, "Diff not found")
    if diff.status != "pending":
        raise HTTPException(409, f"Diff already {diff.status}")
    diff.status = "dismissed"
    diff.resolved_by = body.resolved_by
    diff.resolved_at = datetime.now()
    await db.commit()
    return {"diff_id": diff_id, "status": "dismissed"}


class ExplorePathItem(BaseModel):
    """单条待探索路径：路径 + 可选描述。"""
    path: str
    description: str | None = None


class ExploreRequest(BaseModel):
    """AI 自动探索请求（7.3.1 AI 探索模式）。

    选择已配置的 PC 端地址，补充一条或多条需要探索的路径（描述可选）。
    overwrite=False 时遇到已缓存路径会跳过并在 existing 中返回，供前端弹框确认；
    overwrite=True（或仅传需重新缓存的路径）时强制重新缓存。
    """
    project_id: str
    base_url: str  # 已配置的 PC 端基础地址，如 https://app.example.test
    paths: list[ExplorePathItem] = []
    overwrite: bool = False


def _make_region(name: str, selector: str, elements: list[dict]) -> dict:
    return {"name": name, "selector": selector, "elements": elements}


def _page_for_path(path: str, description: str | None) -> dict:
    """为指定路径生成页面名与通用 DOM 区块描述（MOCK 模式）。

    真实模式可替换为 Playwright 按 base_url + path 实际爬取而无需改动接口签名。
    page_name 固定取路径；描述单独保存到 description 字段（填过则有，否则空）。
    """
    return {
        "page_name": path,
        "description": description.strip() if description and description.strip() else None,
        "regions": [
            _make_region("页面主体", ".page-container, .ant-layout-content", [
                {"name": "标题区", "selector": ".page-header, h1, h2", "type": "header"},
                {"name": "操作按钮", "selector": ".action-btn, button", "type": "button"},
            ]),
            _make_region("内容区域", ".ant-table-wrapper, form.ant-form, .ant-descriptions", [
                {"name": "表格/表单主体", "selector": ".ant-table-tbody, .ant-form-item, .ant-descriptions-row", "type": "content"},
            ]),
        ],
    }


@router.post("/explore")
async def explore_pages(body: ExploreRequest, db: AsyncSession = Depends(get_db)):
    """AI 自动探索 PC 端页面结构，按用户补充的路径写入共享缓存（7.3.1）。

    遍历 paths：未缓存的直接写入；已缓存的——overwrite=False 时收集到 existing 跳过，
    供前端弹框确认是否重新缓存；overwrite=True 时强制刷新结构与 hash。
    """
    proj = await db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    if not body.paths:
        raise HTTPException(400, "请至少补充一条需要探索的路径")

    created = 0
    updated = 0
    existing_paths: list[dict] = []
    result_dicts: list[dict] = []
    failed_paths: list[dict] = []

    from app.services.page_explorer import explore as explore_page

    for item in body.paths:
        raw_path = (item.path or "").strip()  # 用户填的中文页面名
        if not raw_path:
            continue

        # 按「页面名称」去重(用户给页面命名，同名即同一逻辑页面)
        existing = (await db.execute(
            select(PageStructureCache).where(
                PageStructureCache.project_id == body.project_id,
                PageStructureCache.page_name == raw_path,
            )
        )).scalars().first()

        if existing and not body.overwrite:
            existing_paths.append({"path": raw_path, "url_pattern": existing.url_pattern, "page_name": existing.page_name})
            continue

        # AI 按「页面名 + 操作描述」自动导航到目标页，抽取导航目录 + 页面结构
        explored = await explore_page(body.base_url, raw_path, item.description)
        has_els = bool(explored) and any((reg.get("elements") for reg in (explored.get("regions") or [])))
        final_url = (explored or {}).get("final_url")
        # url_pattern 用真实页面 URL 路径(导航到达的页面)，取不到再回退页面名
        pattern = normalize_url(final_url) if final_url else normalize_url(raw_path)
        if not has_els:
            page = _page_for_path(raw_path, item.description)
            page_name = raw_path  # 始终用用户填的中文名
            regions = (explored or {}).get("regions") or page["regions"]
            if not explored:
                failed_paths.append({"path": raw_path})
        else:
            page_name = raw_path
            regions = explored["regions"]
        desc = item.description.strip() if item.description and item.description.strip() else None
        dom_hash = compute_region_hashes(regions)

        if existing:
            existing.base_url = body.base_url
            existing.url_pattern = pattern
            existing.page_name = page_name
            existing.description = desc
            existing.dom_hash = dom_hash
            existing.regions = regions
            existing.status = "active"
            existing.updated_at = datetime.now()
            result_dicts.append(_to_dict(existing))
            updated += 1
        else:
            entry = PageStructureCache(
                project_id=body.project_id,
                base_url=body.base_url,
                url_pattern=pattern,
                page_name=page_name,
                description=desc,
                dom_hash=dom_hash,
                regions=regions,
                status="active",
            )
            db.add(entry)
            await db.flush()
            await db.refresh(entry)
            result_dicts.append(_to_dict(entry))
            created += 1

    await db.commit()
    return {
        "base_url": body.base_url,
        "explored_count": created + updated,
        "created_count": created,
        "updated_count": updated,
        "failed_paths": failed_paths,
        "existing_paths": existing_paths,
        "entries": result_dicts,
    }


class RecordRequest(BaseModel):
    """人工录入——Playwright 录制请求（7.3.1 自动探索失败的兜底）。

    录制前在前端选择 PC 端基础地址；后端以 PC 桌面视口启动 `playwright codegen`，
    弹出真实浏览器，由当前登录人自主操作（打开即开始、关闭即结束录制），
    录制完成后把访问过的页面结构写入共享缓存。
    """
    project_id: str
    base_url: str            # 已配置的 PC 端基础地址
    start_path: str | None = None  # 可选起始路径，浏览器打开后直接落在该页
    overwrite: bool = False  # 已存在的页面是否覆盖


@router.get("/recorder/status")
async def recorder_status():
    """前端用于判断本机是否具备录制能力（决定是否禁用录制按钮）。"""
    cli = playwright_cli()
    return {"available": cli is not None, "cli_path": cli}


@router.post("/record")
async def record_pages_endpoint(body: RecordRequest, db: AsyncSession = Depends(get_db)):
    """启动 Playwright 录制并将录制到的页面结构写入共享缓存（人工录入）。

    阻塞直到当前登录人关闭录制浏览器；随后逐页 upsert：未缓存的直接写入，
    已缓存的——overwrite=False 收集到 existing 跳过，overwrite=True 覆盖刷新。
    """
    proj = await db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    # codegen 是阻塞式有头浏览器，丢到线程池跑，避免卡住事件循环
    import anyio
    try:
        entries = await anyio.to_thread.run_sync(record_pages, body.base_url, body.start_path)
    except RecorderError as e:
        raise HTTPException(400, str(e))

    created = 0
    updated = 0
    existing_paths: list[dict] = []
    result_dicts: list[dict] = []

    for page in entries:
        pattern = normalize_url(page["source_url"])
        regions = page["regions"]
        dom_hash = compute_region_hashes(regions)

        existing = (await db.execute(
            select(PageStructureCache).where(
                PageStructureCache.project_id == body.project_id,
                PageStructureCache.url_pattern == pattern,
            )
        )).scalars().first()

        if existing and not body.overwrite:
            existing_paths.append({"url_pattern": pattern, "page_name": existing.page_name})
            continue

        if existing:
            existing.base_url = body.base_url
            existing.page_name = pattern
            existing.dom_hash = dom_hash
            existing.regions = regions
            existing.status = "active"
            existing.updated_at = datetime.now()
            result_dicts.append(_to_dict(existing))
            updated += 1
        else:
            entry = PageStructureCache(
                project_id=body.project_id,
                base_url=body.base_url,
                url_pattern=pattern,
                page_name=pattern,
                dom_hash=dom_hash,
                regions=regions,
                status="active",
            )
            db.add(entry)
            await db.flush()
            await db.refresh(entry)
            result_dicts.append(_to_dict(entry))
            created += 1

    await db.commit()
    return {
        "base_url": body.base_url,
        "recorded_count": created + updated,
        "created_count": created,
        "updated_count": updated,
        "existing_paths": existing_paths,
        "entries": result_dicts,
    }


@router.post("/cleanup")
async def cleanup_stale_caches(project_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """清理超 STALE_AFTER_DAYS 天未被命中的缓存（7.3.6 自动清理，可挂 cron 定时调用）。

    以 last_hit_at 为基准；从未命中过的条目以 captured_at 兜底，避免漏判。
    """
    q = select(PageStructureCache)
    if project_id:
        q = q.where(PageStructureCache.project_id == project_id)
    caches = (await db.execute(q)).scalars().all()

    now = datetime.now(timezone.utc)
    removed: list[str] = []
    for c in caches:
        basis = c.last_hit_at or c.captured_at
        if is_stale(basis, now):
            removed.append(c.id)
            await db.delete(c)
    await db.commit()
    return {"removed": len(removed), "removed_ids": removed, "stale_after_days": STALE_AFTER_DAYS}


# 动态单参 GET 放最后注册，避免吞掉 /diffs 等静态子路径
@router.get("/{cache_id}")
async def get_page_cache(cache_id: str, db: AsyncSession = Depends(get_db)):
    entry = await db.get(PageStructureCache, cache_id)
    if not entry:
        raise HTTPException(404, "Cache entry not found")
    return _to_dict(entry)


def _diff_to_dict(d: PageCacheDiff) -> dict:
    return {
        "id": d.id,
        "project_id": d.project_id,
        "cache_id": d.cache_id,
        "url_pattern": d.url_pattern,
        "page_name": d.page_name,
        "changed_regions": d.changed_regions,
        "new_regions": d.new_regions,
        "new_dom_hash": d.new_dom_hash,
        "status": d.status,
        "resolved_by": d.resolved_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
    }


def _to_dict(c: PageStructureCache) -> dict:
    region_count = len(c.regions) if c.regions else 0
    element_count = sum(len(r.get("elements", [])) for r in (c.regions or []))
    return {
        "id": c.id,
        "project_id": c.project_id,
        "base_url": c.base_url,
        "url_pattern": c.url_pattern,
        "page_name": c.page_name,
        "description": c.description,
        "dom_hash": c.dom_hash,
        "regions": c.regions,
        "region_count": region_count,
        "element_count": element_count,
        "captured_at": c.captured_at.isoformat() if c.captured_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "last_hit_at": c.last_hit_at.isoformat() if c.last_hit_at else None,
        "hit_count": c.hit_count,
        "status": c.status,
    }
