import copy
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Requirement, RequirementSlice, TestCase, Defect, TestResult, Execution
from app.schemas import (
    RequirementCreate, RequirementOut, RequirementUpdate,
    ConfirmationPointUpdate, FeishuLinkSyncRequest, BatchNoConfirmRequest,
    SliceCreate, SliceUpdate, SliceOut,
)
from app.services.dashboard_metrics import (
    _evaluate_releasability, OPEN_DEFECT_STATUSES, FIXED_DEFECT_STATUSES,
)
from app.services.severity import get_blocking_severity
from app.services.document_parser import DOCUMENT_EXTENSIONS, IMAGE_EXTENSIONS, derive_title, extract_text_from_file
from app.services.feishu_app import fetch_bitable_requirements, fetch_feishu_requirement_by_link, FeishuError
from app.services import external_tasks
from app.services.external_tasks import ExternalTaskError
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

UPLOADS_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


async def _resolve_owner_name(db: AsyncSession, current_user: dict | None) -> str | None:
    """归属人=把需求添加/同步到平台的登录人姓名。优先用平台用户记录的姓名，回退 token 里的 name。"""
    try:
        from app.services.ai_key import get_user_record
        u = await get_user_record(db, current_user)
        if u and u.name:
            return u.name
    except Exception:
        pass
    return (current_user or {}).get("name")


# ── 需求切片 CRUD（多人多范围：阶段1，纯增量，不改现有分析/生成读写）────────────
@router.get("/{req_id}/slices", response_model=list[SliceOut])
async def list_slices(req_id: str, db: AsyncSession = Depends(get_db)):
    """列出某需求的所有切片(默认全文切片排在最前)。

    默认切片是「需求级数据的读取视图」：分析结果/确认/状态实时取自需求本身，
    保证默认切片与需求级既有逻辑(分析/生成/确认仍写需求)始终一致、不发生分叉。
    """
    req = await db.get(Requirement, req_id)
    await ensure_default_slice(db, req_id)  # 新建需求兜底建默认全文切片
    rows = (await db.execute(
        select(RequirementSlice)
        .where(RequirementSlice.requirement_id == req_id)
        .order_by(RequirementSlice.is_default.desc(), RequirementSlice.created_at.asc())
    )).scalars().all()
    out: list[SliceOut] = []
    for sl in rows:
        item = SliceOut.model_validate(sl)
        item.has_pending = bool(sl.pending_scope)
        if sl.is_default and req is not None:
            item.analysis_result = req.analysis_result
            item.analysis_confirmation = req.analysis_confirmation
            item.status = req.status
        out.append(item)
    return out


_SCOPE_SEP = "\n\n———\n\n"  # 累加圈选片段之间的分隔


async def ensure_default_slice(db: AsyncSession, req_id: str) -> "RequirementSlice | None":
    """确保该需求存在默认「全文」切片(新建需求不再依赖迁移)。已存在则直接返回。"""
    sl = (await db.execute(
        select(RequirementSlice).where(
            RequirementSlice.requirement_id == req_id, RequirementSlice.is_default.is_(True)
        )
    )).scalars().first()
    if sl:
        return sl
    req = await db.get(Requirement, req_id)
    if not req:
        return None
    sl = RequirementSlice(
        requirement_id=req_id, scope_label="全文", is_default=True,
        owner_name=req.owner_name, status=req.status or "pending_analysis",
    )
    db.add(sl)
    await db.commit()
    await db.refresh(sl)
    return sl


def _dedup_append_scope(existing: str, new: str) -> tuple[str | None, str]:
    """把新圈选并入已有范围，去重叠。返回 (新的累加scope, 实际新增文本)；(None,'')=整段已覆盖、跳过。
    覆盖四种重叠：① new 被 existing 包含 → 跳过；② new 包含 existing → 用 new 替换、增量=new 去掉 existing；
    ③ 正向重叠(existing 结尾==new 开头)；④ 反向重叠(existing 开头==new 结尾)。"""
    new = (new or "").strip()
    if not new:
        return None, ""
    if new in existing:                      # ① 完全被包含
        return None, ""
    if existing and existing in new:         # ② new 完全包含 existing → 用更大的 new
        idx = new.find(existing)
        added = (new[:idx] + new[idx + len(existing):]).strip()
        return new, (added or new)
    cand = new
    # ③ 去掉 cand 开头 与 existing 结尾 的最长重叠
    for k in range(min(len(existing), len(cand)), 0, -1):
        if existing.endswith(cand[:k]):
            cand = cand[k:]
            break
    # ④ 去掉 cand 结尾 与 existing 开头 的最长重叠
    for k in range(min(len(existing), len(cand)), 0, -1):
        if existing.startswith(cand[-k:]):
            cand = cand[:-k]
            break
    cand = cand.strip()
    if not cand:
        return None, ""
    return (existing + _SCOPE_SEP + cand if existing else cand), cand


@router.post("/{req_id}/slices", response_model=SliceOut, status_code=201)
async def create_slice(req_id: str, body: SliceCreate, db: AsyncSession = Depends(get_db),
                       current_user: dict = Depends(get_current_user)):
    """新建/累加负责范围：当前登录人在该需求已有范围时，把这次圈选【追加】进他的范围
    (scope_text 累加 + 记入 pending_scope 增量)；否则新建一个范围。归属默认=当前登录人。"""
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "需求不存在")
    await ensure_default_slice(db, req_id)  # 保证默认全文切片始终存在
    owner = body.owner_name or await _resolve_owner_name(db, current_user)
    new_text = (body.scope_text or "").strip()

    existing = (await db.execute(
        select(RequirementSlice).where(
            RequirementSlice.requirement_id == req_id,
            RequirementSlice.is_default.is_(False),
            RequirementSlice.owner_name == owner,
        ).order_by(RequirementSlice.created_at.asc())
    )).scalars().first()

    if existing is not None:
        # 追加进我的范围：去重叠后把真正新增的部分并入 scope_text，并记入 pending_scope(供增量分析)
        merged, added = _dedup_append_scope(existing.scope_text or "", new_text)
        appended = merged is not None
        if appended:
            existing.scope_text = merged
            existing.pending_scope = (existing.pending_scope + _SCOPE_SEP + added) if existing.pending_scope else added
            tokens = list(existing.scope_image_tokens or []) + list(body.scope_image_tokens or [])
            existing.scope_image_tokens = list(dict.fromkeys(tokens)) or None
            await db.commit()
            await db.refresh(existing)
        out = SliceOut.model_validate(existing)
        out.has_pending = bool(existing.pending_scope)
        out.appended = appended  # False=这段与已有范围重叠、无新增，被跳过，供前端提示
        return out

    sl = RequirementSlice(
        requirement_id=req_id,
        scope_label=(body.scope_label or "全文").strip() or "全文",
        scope_text=new_text or None,
        pending_scope=new_text or None,
        scope_image_tokens=body.scope_image_tokens,
        owner_name=owner,
        status="pending_analysis",
        is_default=False,
    )
    db.add(sl)
    await db.commit()
    await db.refresh(sl)
    out = SliceOut.model_validate(sl)
    out.has_pending = bool(sl.pending_scope)
    return out


@router.patch("/slices/{slice_id}", response_model=SliceOut)
async def update_slice(slice_id: str, body: SliceUpdate, db: AsyncSession = Depends(get_db)):
    """改切片的范围名/范围/归属人。"""
    sl = await db.get(RequirementSlice, slice_id)
    if not sl:
        raise HTTPException(404, "切片不存在")
    if body.scope_label is not None:
        sl.scope_label = body.scope_label.strip() or sl.scope_label
    if body.scope_text is not None:
        sl.scope_text = body.scope_text
    if body.scope_image_tokens is not None:
        sl.scope_image_tokens = body.scope_image_tokens
    if body.owner_name is not None:
        sl.owner_name = body.owner_name
    await db.commit()
    await db.refresh(sl)
    return sl


@router.delete("/slices/{slice_id}", status_code=200)
async def delete_slice(slice_id: str, db: AsyncSession = Depends(get_db)):
    """删除切片：默认全文切片不可删；其下用例解绑(slice_id 置空，仍属该需求)。"""
    sl = await db.get(RequirementSlice, slice_id)
    if not sl:
        raise HTTPException(404, "切片不存在")
    if sl.is_default:
        raise HTTPException(400, "默认「全文」切片不可删除")
    cases = (await db.execute(select(TestCase).where(TestCase.slice_id == slice_id))).scalars().all()
    for tc in cases:
        tc.slice_id = None
    await db.delete(sl)
    await db.commit()
    return {"status": "ok", "unlinked_cases": len(cases)}


@router.get("/{req_id}/coverage")
async def requirement_coverage(req_id: str, db: AsyncSession = Depends(get_db),
                               current_user: dict = Depends(get_current_user)):
    """按需的需求覆盖分析(漏测检测)：对比需求与现有用例，给出覆盖率与未覆盖功能点。"""
    from app.services.ai_key import apply_user_ai_key_soft
    await apply_user_ai_key_soft(db, current_user)  # 有 key 用发起人的，无则回退全局(过渡期不阻断)
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "需求不存在")
    cases = (await db.execute(
        select(TestCase).where(
            TestCase.requirement_id == req_id,
            TestCase.deleted_at.is_(None),
            TestCase.review_status.is_(None),
        )
    )).scalars().all()
    titles = [c.title for c in cases]
    # 覆盖分析按"最近一次分析的文档范围"比对(选区分析则只看那段)，无则回退全文
    scope = (req.analysis_result or {}).get("scope_text") or req.content
    from app.agents import RequirementAnalystAgent
    agent = RequirementAnalystAgent()
    result = await agent.analyze_coverage(req.title, scope, req.analysis_confirmation, titles)
    result["case_count"] = len(titles)
    result["scoped"] = bool((req.analysis_result or {}).get("scoped"))
    return result


def _build_analysis_confirmation(analysis_result: dict) -> str | None:
    parts: list[str] = []
    i = 1
    for ip in analysis_result.get("issue_points", []):
        for cp in ip.get("confirmation_points", []):
            if cp.get("status") == "confirmed":
                result_text = "无需确认" if cp.get("no_confirmation_needed") else (cp.get("confirmation") or "无需确认")
                parts.append(f"需确认点{i}: {cp['content']}\n确认结果: {result_text}")
                i += 1
    return "\n\n".join(parts) if parts else None


@router.get("", response_model=list[RequirementOut])
async def list_requirements(
    project_id: str | None = None,
    iteration: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # 数据可见范围：普通用户只看自己创建的需求(强制归属人=本人，忽略传入 owner)，管理员不受限。
    from app.services.data_scope import enforce_owner
    owner = await enforce_owner(db, current_user, owner)
    q = select(Requirement).order_by(Requirement.created_at.desc())
    if project_id:
        q = q.where(Requirement.project_id == project_id)
    if iteration:
        q = q.where(Requirement.iteration == iteration)
    if status:
        q = q.where(Requirement.status == status)
    if owner:
        # 归属人筛选：__unassigned__ 取未分配；否则按姓名精确匹配
        if owner == "__unassigned__":
            q = q.where(Requirement.owner_name.is_(None))
        else:
            q = q.where(Requirement.owner_name == owner)
    reqs = (await db.execute(q)).scalars().all()
    # 附「负责范围」数(不含默认全文)，供列表决定是否可展开
    if reqs:
        from sqlalchemy import func as _func
        counts = dict((await db.execute(
            select(RequirementSlice.requirement_id, _func.count())
            .where(
                RequirementSlice.requirement_id.in_([r.id for r in reqs]),
                RequirementSlice.is_default.is_(False),
            ).group_by(RequirementSlice.requirement_id)
        )).all())
        for r in reqs:
            r.slice_count = counts.get(r.id, 0)
    return reqs


@router.post("", response_model=RequirementOut, status_code=201)
async def create_requirement(body: RequirementCreate, db: AsyncSession = Depends(get_db),
                             current_user: dict = Depends(get_current_user)):
    req = Requirement(**body.model_dump())
    req.owner_name = await _resolve_owner_name(db, current_user)
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


@router.post("/upload", response_model=RequirementOut, status_code=201)
async def upload_requirement(
    project_id: str = Form(...),
    product_line: str | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """上传需求文档(.txt/.md/.docx/.pdf)或需求图片，自动解析/存储并创建需求。"""
    filename = file.filename or "未命名"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    file_bytes = await file.read()
    owner_name = await _resolve_owner_name(db, current_user)

    if ext in IMAGE_EXTENSIONS:
        # 图片需求：直接存储文件，不做OCR识别
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        req = Requirement(
            project_id=project_id,
            title=title,
            content="",
            product_line=product_line,
            source="upload",
            owner_name=owner_name,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)

        stored_name = f"{req.id}.{ext}"
        (UPLOADS_DIR / stored_name).write_bytes(file_bytes)
        req.attachment_path = stored_name
        await db.commit()
        await db.refresh(req)
        return req

    elif ext in DOCUMENT_EXTENSIONS:
        if not file_bytes:
            raise HTTPException(400, "上传的文件为空，请检查文件后重试")
        try:
            content = extract_text_from_file(filename, file_bytes)
        except Exception as e:
            raise HTTPException(400, f"解析文件失败（.{ext}）：{e}")
        title = derive_title(content, filename)
        if not content.strip():
            raise HTTPException(400, "未能从文件中提取到有效内容，请确认文档不是扫描件/空白文档")
        req = Requirement(
            project_id=project_id,
            title=title,
            content=content,
            product_line=product_line,
            source="upload",
            owner_name=owner_name,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)
        return req

    else:
        raise HTTPException(400, f"不支持的文件类型「.{ext}」，仅支持文档(.txt/.md/.docx/.pdf)或图片(.png/.jpg/.jpeg)")


@router.get("/media/{img_token}")
async def get_feishu_media(img_token: str):
    """返回从飞书文档同步下来的内联图片（按图片 token）。"""
    from app.services.feishu_app import _MEDIA_DIR
    if not img_token.isalnum():
        raise HTTPException(400, "无效的图片标识")
    matches = list(_MEDIA_DIR.glob(f"{img_token}.*")) if _MEDIA_DIR.exists() else []
    if not matches:
        raise HTTPException(404, "图片不存在")
    fp = matches[0]
    media_type = mimetypes.guess_type(str(fp))[0] or "image/png"
    return FileResponse(str(fp), media_type=media_type)


@router.get("/attachment/{req_id}")
async def get_requirement_attachment(req_id: str, db: AsyncSession = Depends(get_db)):
    """返回图片需求的原始文件。"""
    req = await db.get(Requirement, req_id)
    if not req or not req.attachment_path:
        raise HTTPException(404, "No attachment found")
    file_path = UPLOADS_DIR / req.attachment_path
    if not file_path.exists():
        raise HTTPException(404, "Attachment file not found on disk")
    media_type = mimetypes.guess_type(req.attachment_path)[0] or "application/octet-stream"
    return FileResponse(str(file_path), media_type=media_type)


@router.post("/sync-feishu", response_model=list[RequirementOut], status_code=201)
async def sync_feishu_requirements(project_id: str, db: AsyncSession = Depends(get_db),
                                   current_user: dict = Depends(get_current_user)):
    """从飞书多维表格拉取需求记录，按record_id去重后创建新的Requirement(source="feishu")。"""
    try:
        records = await fetch_bitable_requirements()
    except FeishuError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"飞书批量同步失败：{e}")
    owner_name = await _resolve_owner_name(db, current_user)

    existing = set((await db.execute(
        select(Requirement.source_record_id).where(
            Requirement.project_id == project_id,
            Requirement.source_record_id.is_not(None),
        )
    )).scalars().all())

    created = []
    for rec in records:
        if rec["record_id"] in existing:
            continue
        req = Requirement(
            project_id=project_id,
            title=rec["title"],
            content=rec["content"],
            product_line=rec.get("product_line"),
            iteration=rec.get("iteration"),
            source="feishu",
            source_record_id=rec["record_id"],
            owner_name=owner_name,
        )
        db.add(req)
        created.append(req)

    await db.commit()
    for req in created:
        await db.refresh(req)
    return created


@router.post("/sync-feishu-link", response_model=RequirementOut, status_code=201)
async def sync_feishu_requirement_by_link(project_id: str, body: FeishuLinkSyncRequest, db: AsyncSession = Depends(get_db),
                                          current_user: dict = Depends(get_current_user)):
    """根据飞书链接同步单条需求(source="feishu")，按record_id去重。
    支持多维表格记录链接(/base/...)与知识库文档链接(/wiki/...)。"""
    try:
        record = await fetch_feishu_requirement_by_link(body.link)
    except FeishuError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"飞书同步失败：{e}")
    if not record:
        raise HTTPException(400, "无法解析或获取该飞书链接对应的内容（仅支持多维表格记录链接与知识库文档链接）")

    existing = (await db.execute(
        select(Requirement).where(
            Requirement.project_id == project_id,
            Requirement.source_record_id == record["record_id"],
        )
    )).scalars().first()
    if existing:
        # 已同步则刷新内容（重新拉取飞书最新原文），不报错
        existing.title = record["title"]
        existing.content = record["content"]
        existing.product_line = record.get("product_line") or existing.product_line
        existing.iteration = record.get("iteration") or existing.iteration
        await db.commit()
        await db.refresh(existing)
        return existing

    req = Requirement(
        project_id=project_id,
        title=record["title"],
        content=record["content"],
        product_line=record.get("product_line"),
        iteration=record.get("iteration"),
        source="feishu",
        source_record_id=record["record_id"],
        owner_name=await _resolve_owner_name(db, current_user),
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


@router.get("/external-system/projects")
async def external_system_projects():
    """列出外部任务系统中的可见项目，供选择同步源。"""
    try:
        return await external_tasks.list_projects()
    except ExternalTaskError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"获取来源项目失败：{e}")


@router.post("/sync-external", response_model=list[RequirementOut], status_code=201)
async def sync_external_requirements(
    project_id: str,
    external_project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """根据项目批量拉取需求到当前平台项目，按id去重。"""
    try:
        records = await external_tasks.fetch_requirements(external_project_id)
    except ExternalTaskError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"批量同步需求失败：{e}")
    owner_name = await _resolve_owner_name(db, current_user)

    records = [r for r in records if (r.get("type") or "requirement") == "requirement"]

    existing = set((await db.execute(
        select(Requirement.source_record_id).where(
            Requirement.project_id == project_id,
            Requirement.source == "external",
        )
    )).scalars().all())

    created = []
    for rec in records:
        rid = rec.get("id")
        if not rid or rid in existing:
            continue
        req = Requirement(
            project_id=project_id,
            title=rec.get("title") or "未命名需求",
            content=rec.get("description") or "",
            product_line=rec.get("product_line_name"),
            iteration=rec.get("target_release_id"),
            source="external",
            source_record_id=rid,
            owner_name=owner_name,
        )
        db.add(req)
        created.append(req)
    await db.commit()
    for r in created:
        await db.refresh(r)
    return created


@router.get("/{req_id}", response_model=RequirementOut)
async def get_requirement(req_id: str, db: AsyncSession = Depends(get_db)):
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")
    return req


@router.get("/{req_id}/detail")
async def get_requirement_detail(req_id: str, db: AsyncSession = Depends(get_db)):
    """需求详情抽屉（5.2.5）：在需求基本信息上聚合关联用例、缺陷、执行记录与可发布判定。

    用于看板点击需求行后从右侧滑出的详情抽屉，不跳转新页面。
    """
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")

    # 关联用例（排除软删除）
    cases = (await db.execute(
        select(TestCase).where(
            TestCase.requirement_id == req_id,
            TestCase.deleted_at.is_(None),
        ).order_by(TestCase.priority, TestCase.case_id)
    )).scalars().all()

    total = len(cases)
    passed = sum(1 for c in cases if c.last_status == "passed")
    failed = sum(1 for c in cases if c.last_status == "failed")
    skipped = sum(1 for c in cases if c.last_status == "skipped")
    not_run = sum(1 for c in cases if c.last_status == "not_run")
    pass_rate = (passed / total * 100) if total > 0 else 0.0
    p0_failed = sum(1 for c in cases if c.priority == "P0" and c.last_status == "failed")

    case_rows = [
        {
            "id": c.id,
            "case_id": c.case_id,
            "title": c.title,
            "priority": c.priority,
            "platforms": c.platforms or [],
            "modules": c.modules or [],
            "last_status": c.last_status,
            "is_automated": c.is_automated,
        }
        for c in cases
    ]

    # 关联缺陷
    case_ids = [c.id for c in cases]
    defects = (await db.execute(
        select(Defect).where(Defect.test_case_id.in_(case_ids))
    )).scalars().all() if case_ids else []

    open_defects = [d for d in defects if d.status in OPEN_DEFECT_STATUSES]
    fixed_defects = [d for d in defects if d.status in FIXED_DEFECT_STATUSES]
    # 按缺陷等级（用户枚举）动态统计；最高等级未关闭数用于发布阻断
    from collections import Counter
    blocking_level = await get_blocking_severity(db)
    sev_total_ct = Counter(d.severity for d in defects)
    sev_open_ct = Counter(d.severity for d in open_defects)
    critical_open = sev_open_ct.get(blocking_level, 0)
    severity_breakdown = {
        k: {"total": int(sev_total_ct.get(k, 0)), "open": int(sev_open_ct.get(k, 0))}
        for k in set(sev_total_ct) | set(sev_open_ct)
    }

    defect_rows = [
        {
            "id": d.id,
            "title": d.title,
            "severity": d.severity,
            "confidence": d.confidence,
            "status": d.status,
            "closed": d.status in FIXED_DEFECT_STATUSES,
            "feishu_ticket_id": d.feishu_ticket_id,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in defects
    ]

    # 近期执行记录（该需求名下用例参与的执行，按时间倒序取近 10 条）
    if case_ids:
        result_rows = (await db.execute(
            select(TestResult, Execution)
            .join(Execution, TestResult.execution_id == Execution.id)
            .where(TestResult.test_case_id.in_(case_ids))
            .order_by(TestResult.created_at.desc())
            .limit(10)
        )).all()
        executions = [
            {
                "result_id": tr.id,
                "execution_id": ex.id,
                "execution_name": ex.name,
                "test_case_id": tr.test_case_id,
                "status": tr.status,
                "failure_type": tr.failure_type,
                "created_at": tr.created_at.isoformat() if tr.created_at else None,
            }
            for tr, ex in result_rows
        ]
    else:
        executions = []

    # 可发布判定（复用看板统一规则：测试进度100% + 2级bug数为0）
    releasability, blocking_reasons = _evaluate_releasability(
        total=total,
        not_run=not_run,
        pass_rate=pass_rate,
        p0_open=critical_open,
    )

    return {
        "requirement": {
            "id": req.id,
            "project_id": req.project_id,
            "title": req.title,
            "content": req.content,
            "product_line": req.product_line,
            "iteration": req.iteration,
            "source": req.source,
            "source_record_id": req.source_record_id,
            "status": req.status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "updated_at": req.updated_at.isoformat() if req.updated_at else None,
        },
        "case_quality": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "not_run": not_run,
            "pass_rate": round(pass_rate, 1),
        },
        "cases": case_rows,
        "defects": defect_rows,
        "defect_summary": {
            "total": len(defects),
            "open": len(open_defects),
            "fixed": len(fixed_defects),
            "critical_open": critical_open,
            "severity_breakdown": severity_breakdown,
        },
        "executions": executions,
        "releasability": releasability,
        "blocking_reasons": blocking_reasons,
    }


@router.patch("/{req_id}", response_model=RequirementOut)
async def update_requirement(req_id: str, body: RequirementUpdate, db: AsyncSession = Depends(get_db)):
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(req, k, v)
    await db.commit()
    await db.refresh(req)
    return req


@router.post("/{req_id}/complete", response_model=RequirementOut)
async def complete_requirement(req_id: str, db: AsyncSession = Depends(get_db)):
    """手动将需求标记为已完成。"""
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")
    req.status = "done"
    await db.commit()
    await db.refresh(req)
    return req


@router.patch("/{req_id}/confirmation-points/{point_id}", response_model=RequirementOut)
async def update_confirmation_point(
    req_id: str, point_id: str, body: ConfirmationPointUpdate,
    slice_id: str | None = None, db: AsyncSession = Depends(get_db),
):
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")

    # 非默认切片：在切片自己的分析结果上确认；默认/未指定：走需求级(既有行为)
    target = None
    if slice_id:
        sl = await db.get(RequirementSlice, slice_id)
        if sl and not sl.is_default:
            target = sl
    holder = target if target is not None else req
    if not holder.analysis_result:
        raise HTTPException(400, "尚未进行需求分析")

    analysis_result = copy.deepcopy(holder.analysis_result)
    found = False
    for ip in analysis_result.get("issue_points", []):
        for cp in ip.get("confirmation_points", []):
            if cp.get("point_id") == point_id:
                cp["status"] = "confirmed"
                cp["no_confirmation_needed"] = body.no_confirmation_needed
                cp["confirmation"] = None if body.no_confirmation_needed else body.confirmation
                found = True
                break
        if found:
            break
    if not found:
        raise HTTPException(404, "Confirmation point not found")

    holder.analysis_result = analysis_result
    holder.analysis_confirmation = _build_analysis_confirmation(analysis_result)
    await db.commit()
    await db.refresh(req)
    return req


@router.post("/{req_id}/confirmation-points/batch-no-confirm", response_model=RequirementOut)
async def batch_no_confirm_points(
    req_id: str, body: BatchNoConfirmRequest,
    slice_id: str | None = None, db: AsyncSession = Depends(get_db),
):
    """批量将指定确认点标记为无需确认。非默认切片在切片自己的分析结果上操作。"""
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")
    target = None
    if slice_id:
        sl = await db.get(RequirementSlice, slice_id)
        if sl and not sl.is_default:
            target = sl
    holder = target if target is not None else req
    if not holder.analysis_result:
        raise HTTPException(400, "尚未进行需求分析")

    analysis_result = copy.deepcopy(holder.analysis_result)
    for ip in analysis_result.get("issue_points", []):
        for cp in ip.get("confirmation_points", []):
            if cp.get("point_id") in body.point_ids:
                cp["status"] = "confirmed"
                cp["no_confirmation_needed"] = True
                cp["confirmation"] = None

    holder.analysis_result = analysis_result
    holder.analysis_confirmation = _build_analysis_confirmation(analysis_result)
    await db.commit()
    await db.refresh(req)
    return req


@router.delete("/{req_id}", status_code=204)
async def delete_requirement(req_id: str, db: AsyncSession = Depends(get_db)):
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")
    await db.delete(req)
    await db.commit()
