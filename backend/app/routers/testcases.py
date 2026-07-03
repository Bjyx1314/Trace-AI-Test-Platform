from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.dependencies import get_current_user
from app.models import TestCase, TestCaseLog, Project, TestResult, Execution, Defect
from app.schemas import TestCaseCreate, TestCaseOut, CaseReviewAction, BatchCaseReviewAction

router = APIRouter(prefix="/api/testcases", tags=["testcases"])


def _operator_name(current_user: dict | None) -> str:
    """操作记录里的操作人：优先真实姓名(name)，退而取邮箱/账号，兜底"系统"。"""
    cu = current_user or {}
    return cu.get("name") or cu.get("email") or cu.get("sub") or "系统"


async def _generate_case_id(db: AsyncSession, project: Project) -> str:
    prefix = f"TC-{project.case_id_prefix}-"
    result = await db.execute(
        select(TestCase.case_id).where(
            TestCase.project_id == project.id,
            TestCase.case_id.like(f"{prefix}%"),
        )
    )
    max_seq = 0
    for case_id in result.scalars().all():
        suffix = case_id[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


async def _build_case_out(db: AsyncSession, tc: TestCase) -> TestCaseOut:
    out = TestCaseOut.model_validate(tc)
    if tc.review_status == "pending_review" and tc.similar_case_id:
        similar = await db.get(TestCase, tc.similar_case_id)
        if similar:
            out.similar_case_case_id = similar.case_id
            out.similar_case_title = similar.title
    return out


async def _do_review_action(db: AsyncSession, tc: TestCase, action: str, operator: str = "系统") -> None:
    """tc 是新生成的草稿用例(pending_review)。两种处理都落到"库中相似的老用例"上，
    并把老用例纳入当前需求的测试范围，最后丢弃新草稿；都会在老用例的操作记录里留痕：
    - keep(纳入本次测试)：直接复用老用例，不改其内容；
    - update_existing(更新用例库)：用新草稿内容更新老用例后再纳入。
    若找不到可关联的老用例，则退化为把新草稿转正为正式库用例。"""
    existing = await db.get(TestCase, tc.similar_case_id) if tc.similar_case_id else None
    if not existing or existing.deleted_at is not None:
        # 无可复用的老用例：新草稿转正
        tc.review_status = None
        tc.similar_case_id = None
        return

    new_case_id = tc.case_id
    if action == "update_existing":
        existing.title = tc.title
        existing.steps = tc.steps
        existing.expected_result = tc.expected_result
        existing.preconditions = tc.preconditions
        existing.priority = tc.priority
        existing.modules = tc.modules
        existing.platforms = tc.platforms
        existing.case_type = tc.case_type

    # 纳入本次测试范围：把老用例挂到当前需求下（source_req_id 保留原始来源以存血缘）
    existing.requirement_id = tc.requirement_id

    op = "update" if action == "update_existing" else "reuse"
    note = (f"更新用例库并纳入本次测试（来源 {tc.source_req_id}，替换新生成草稿 {new_case_id}）"
            if action == "update_existing"
            else f"复用本用例纳入本次测试（来源 {tc.source_req_id}，丢弃新生成草稿 {new_case_id}）")
    snapshot = _tc_snapshot(existing)
    snapshot["note"] = note

    # 先删草稿再写日志（日志按 existing.id 记录）
    await db.delete(tc)
    await db.flush()
    await _write_log(db, existing, op, snapshot, operator)


def _tc_snapshot(tc: TestCase) -> dict:
    return {
        "case_id": tc.case_id,
        "title": tc.title,
        "priority": tc.priority,
        "case_type": tc.case_type,
        "modules": list(tc.modules or []),
        "platforms": list(tc.platforms or []),
        "expected_result": tc.expected_result,
        "last_status": tc.last_status,
    }


async def _write_log(db: AsyncSession, tc: TestCase, operation: str, snapshot: dict, operator: str = "系统") -> None:
    db.add(TestCaseLog(
        test_case_id=tc.id,
        operation=operation,
        operator=operator,
        snapshot=snapshot,
    ))


@router.get("", response_model=list[TestCaseOut])
async def list_testcases(
    project_id: str | None = None,
    requirement_id: str | None = None,
    priority: str | None = None,
    product_line: str | None = None,
    module: str | None = None,
    platform: str | None = None,
    case_type: str | None = None,
    last_status: str | None = None,
    library_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    # 次级排序键 case_id：批量生成的用例 created_at 相同，仅按时间排会不稳定(编辑后顺序跳动)
    q = select(TestCase).where(TestCase.deleted_at.is_(None)).order_by(TestCase.created_at.desc(), TestCase.case_id.asc())
    # 用例库视图：只看已正式纳入库的用例(需求侧未通过的不在库里)。需求详情视图不传此参数，仍看该需求全部用例。
    if library_only:
        q = q.where(TestCase.in_library.is_(True))
    if project_id:
        q = q.where(TestCase.project_id == project_id)
    if requirement_id:
        q = q.where(TestCase.requirement_id == requirement_id)
    if priority:
        q = q.where(TestCase.priority == priority)
    if product_line:
        q = q.where(TestCase.product_line == product_line)
    if module:
        q = q.where(TestCase.modules.any(module))
    if platform:
        q = q.where(TestCase.platforms.any(platform))
    if case_type:
        q = q.where(TestCase.case_type == case_type)
    if last_status:
        q = q.where(TestCase.last_status == last_status)
    cases = (await db.execute(q)).scalars().all()
    return [await _build_case_out(db, tc) for tc in cases]


@router.get("/trash", response_model=list[TestCaseOut])
async def list_deleted_testcases(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """回收站：已软删除（deleted_at 非空）的用例，按删除时间倒序。"""
    q = (
        select(TestCase)
        .where(TestCase.deleted_at.is_not(None))
        .order_by(TestCase.deleted_at.desc())
    )
    if project_id:
        q = q.where(TestCase.project_id == project_id)
    cases = (await db.execute(q)).scalars().all()
    return [await _build_case_out(db, tc) for tc in cases]


@router.post("", response_model=TestCaseOut, status_code=201)
async def create_testcase(body: TestCaseCreate, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    project = await db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    tc = TestCase(**body.model_dump(), case_id=await _generate_case_id(db, project))
    # 手工新增：无关联需求(直接进库)即纳入用例库；挂到需求下的则等执行通过再纳入
    tc.in_library = tc.requirement_id is None
    db.add(tc)
    await db.flush()
    await _write_log(db, tc, "create", _tc_snapshot(tc), _operator_name(current_user))
    await db.commit()
    await db.refresh(tc)
    return await _build_case_out(db, tc)


@router.post("/batch-review", status_code=200)
async def batch_review_cases(body: BatchCaseReviewAction, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    operator = _operator_name(current_user)
    count = 0
    for case_id in body.case_ids:
        tc = await db.get(TestCase, case_id)
        if not tc or tc.review_status != "pending_review" or tc.deleted_at is not None:
            continue
        await _do_review_action(db, tc, body.action, operator)
        count += 1
    await db.commit()
    return {"status": "ok", "count": count}


@router.get("/export")
async def export_testcases(
    format: str = Query("md", pattern="^(md|xlsx)$"),
    project_id: str | None = None,
    requirement_id: str | None = None,
    ids: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """导出用例为 Markdown 或 Excel。ids(逗号分隔)给定时只导出选中用例，否则按 project_id/requirement_id 过滤。"""
    from app.services import case_io
    q = (
        select(TestCase)
        .where(TestCase.deleted_at.is_(None), TestCase.review_status.is_(None))
        .order_by(TestCase.created_at.desc(), TestCase.case_id.asc())
    )
    id_list = [s for s in (ids or "").split(",") if s.strip()]
    if id_list:
        q = q.where(TestCase.id.in_(id_list))
    elif project_id:
        q = q.where(TestCase.project_id == project_id)
    if requirement_id:
        q = q.where(TestCase.requirement_id == requirement_id)
    cases = (await db.execute(q)).scalars().all()

    if format == "xlsx":
        content = case_io.cases_to_xlsx(cases)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname = "testcases.xlsx"
    else:
        content = case_io.cases_to_markdown(cases).encode("utf-8")
        media = "text/markdown; charset=utf-8"
        fname = "testcases.md"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"}
    return Response(content=content, media_type=media, headers=headers)


@router.post("/import", status_code=200)
async def import_testcases(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    requirement_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """导入用例：支持 xmind / xlsx / md / docx，解析后入库。"""
    from app.services import case_io
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    data = await file.read()
    try:
        parsed = case_io.parse_import(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"文件解析失败：{e}")
    if not parsed:
        raise HTTPException(400, "未从文件解析到任何用例")

    created: list[str] = []
    for cdict in parsed:
        tc = TestCase(
            project_id=project_id,
            requirement_id=requirement_id or None,
            product_line=project.product_line,
            title=cdict["title"],
            priority=cdict["priority"],
            case_type=cdict["case_type"],
            modules=cdict["modules"],
            platforms=cdict["platforms"],
            preconditions=cdict["preconditions"],
            steps=cdict["steps"],
            expected_result=cdict["expected_result"],
            case_id=await _generate_case_id(db, project),
            last_status="not_run",
            script_status="pending",
            # 用例库直接导入(无 requirement_id)→ 直接入库；需求侧导入 → 等执行通过再入库
            in_library=requirement_id is None,
        )
        db.add(tc)
        await db.flush()
        await _write_log(db, tc, "create", _tc_snapshot(tc), _operator_name(current_user))
        created.append(tc.title)
    await db.commit()
    return {"status": "ok", "created": len(created), "titles": created[:50]}


@router.get("/{tc_id}/results")
async def get_testcase_results(tc_id: str, db: AsyncSession = Depends(get_db)):
    """获取指定用例的执行历史记录。"""
    tc = await db.get(TestCase, tc_id)
    if not tc:
        raise HTTPException(404, "TestCase not found")
    rows = (await db.execute(
        select(TestResult, Execution.name.label("exec_name"))
        .join(Execution, TestResult.execution_id == Execution.id)
        .where(TestResult.test_case_id == tc_id)
        .order_by(TestResult.created_at.desc())
    )).all()
    return [
        {
            "id": r.TestResult.id,
            "execution_id": r.TestResult.execution_id,
            "execution_name": r.exec_name,
            "status": r.TestResult.status,
            "duration_ms": r.TestResult.duration_ms,
            "error_message": r.TestResult.error_message,
            "failure_type": r.TestResult.failure_type,
            "defect_status": r.TestResult.defect_status,
            "screenshot_url": r.TestResult.screenshot_url,
            "api_trace": r.TestResult.api_trace,
            "ui_trace": r.TestResult.ui_trace,
            "created_at": r.TestResult.created_at.isoformat() if r.TestResult.created_at else None,
        }
        for r in rows
    ]


@router.get("/{tc_id}/logs")
async def get_testcase_logs(tc_id: str, db: AsyncSession = Depends(get_db)):
    """获取指定用例的CRUD操作记录（新建/修改/删除）。对软删除的用例同样有效。"""
    logs = (await db.execute(
        select(TestCaseLog)
        .where(TestCaseLog.test_case_id == tc_id)
        .order_by(TestCaseLog.created_at.asc())
    )).scalars().all()
    return [
        {
            "id": log.id,
            "operation": log.operation,
            "operator": log.operator,
            "snapshot": log.snapshot,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


@router.get("/{tc_id}", response_model=TestCaseOut)
async def get_testcase(tc_id: str, db: AsyncSession = Depends(get_db)):
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is not None:
        raise HTTPException(404, "TestCase not found")
    return await _build_case_out(db, tc)


@router.post("/{tc_id}/review", status_code=200)
async def review_case(tc_id: str, body: CaseReviewAction, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is not None:
        raise HTTPException(404, "TestCase not found")
    if tc.review_status != "pending_review":
        raise HTTPException(400, "Case is not pending review")
    await _do_review_action(db, tc, body.action, _operator_name(current_user))
    await db.commit()
    return {"status": "ok"}


@router.put("/{tc_id}", response_model=TestCaseOut)
async def update_testcase(tc_id: str, body: TestCaseCreate, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is not None:
        raise HTTPException(404, "TestCase not found")
    for k, v in body.model_dump().items():
        setattr(tc, k, v)
    await db.flush()
    await _write_log(db, tc, "update", _tc_snapshot(tc), _operator_name(current_user))
    await db.commit()
    await db.refresh(tc)
    return await _build_case_out(db, tc)


@router.delete("/{tc_id}", status_code=204)
async def delete_testcase(tc_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is not None:
        raise HTTPException(404, "TestCase not found")
    snapshot = _tc_snapshot(tc)
    tc.deleted_at = datetime.now()
    await _write_log(db, tc, "delete", snapshot, _operator_name(current_user))
    await db.commit()


@router.post("/{tc_id}/manual-pass", response_model=TestCaseOut, status_code=200)
async def manual_pass_testcase(tc_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """手动测试通过：最近结果置为 manual_passed，并自动复核该用例未关闭缺陷为「已解决」。"""
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is not None:
        raise HTTPException(404, "TestCase not found")
    tc.last_status = "manual_passed"
    tc.in_library = True  # 手动测试通过：纳入用例库(单向，永久保留)
    await _write_log(db, tc, "manual_pass", _tc_snapshot(tc), _operator_name(current_user))
    from app.services.defect_review import resolve_open_defects_for_case
    await resolve_open_defects_for_case(db, tc.id, note="手动测试通过，缺陷已解决")
    await db.commit()
    await db.refresh(tc)
    return await _build_case_out(db, tc)


@router.post("/{tc_id}/manual-fail", response_model=TestCaseOut, status_code=200)
async def manual_fail_testcase(tc_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """手动测试失败：最近结果置为 manual_failed，并自动生成一条待复核缺陷。"""
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is not None:
        raise HTTPException(404, "TestCase not found")
    tc.last_status = "manual_failed"
    await _write_log(db, tc, "manual_fail", _tc_snapshot(tc), _operator_name(current_user))
    # 合成一次「手动测试」执行与结果，挂载缺陷(Defect 需关联 execution/test_result)
    ex = Execution(project_id=tc.project_id, name="手动测试", trigger="manual",
                   status="done", total=1, failed=1, execution_mode="manual")
    db.add(ex)
    await db.flush()
    tr = TestResult(execution_id=ex.id, test_case_id=tc.id, status="failed",
                    failure_type="real_defect", error_message="手动测试失败",
                    defect_status="pending_review")
    db.add(tr)
    await db.flush()
    from app.services.defect_review import create_defect_for_failure
    await create_defect_for_failure(db, tr, tc)
    await db.commit()
    await db.refresh(tc)
    return await _build_case_out(db, tc)


@router.post("/{tc_id}/restore", response_model=TestCaseOut, status_code=200)
async def restore_testcase(tc_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """从回收站恢复：清空 deleted_at 并写审计日志。"""
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is None:
        raise HTTPException(404, "已删除的用例不存在")
    tc.deleted_at = None
    await _write_log(db, tc, "restore", _tc_snapshot(tc), _operator_name(current_user))
    await db.commit()
    await db.refresh(tc)
    return await _build_case_out(db, tc)


@router.delete("/{tc_id}/purge", status_code=204)
async def purge_testcase(tc_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """永久物理删除（仅回收站内、且无执行结果/缺陷关联的用例）。"""
    tc = await db.get(TestCase, tc_id)
    if not tc or tc.deleted_at is None:
        raise HTTPException(404, "已删除的用例不存在")

    has_result = (await db.execute(
        select(TestResult.id).where(TestResult.test_case_id == tc_id).limit(1)
    )).first()
    has_defect = (await db.execute(
        select(Defect.id).where(Defect.test_case_id == tc_id).limit(1)
    )).first()
    if has_result or has_defect:
        raise HTTPException(409, "该用例存在执行结果或缺陷记录，无法永久删除")

    await _write_log(db, tc, "purge", _tc_snapshot(tc), _operator_name(current_user))
    await db.delete(tc)
    await db.commit()
