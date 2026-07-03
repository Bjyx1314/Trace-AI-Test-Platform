import logging

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct
from app.database import get_db
from app.models import Execution, TestResult, TestCase, Requirement
from app.schemas import ExecutionCreate, ExecutionOut, TestResultOut, DefectReviewUpdate
from app.services.mock_runner import MockExecutionRunner
from app.config import settings
from app.dependencies import get_current_user
from app.services.ai_key import resolve_user_ai_key, NoAiKeyError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.get("/app-package-file")
async def download_app_package_file(key: str):
    """按 key 下发 apk 安装包（worker 执行换包时从平台下载）。文件放服务器 worker-dist/<key>.apk。

    真实接口接入后由外部 CDN/服务提供下载链接；此处先用平台自带的 worker-dist 目录托管测试 apk。
    """
    import os as _os
    from fastapi.responses import FileResponse
    safe = _os.path.basename(key or "")
    if not safe:
        raise HTTPException(400, "非法 key")
    base = _os.path.dirname(settings.worker_exe_path)  # 与 worker 二进制同目录(worker-dist，已挂载持久)
    path = _os.path.join(base, f"{safe}.apk")
    if not _os.path.isfile(path):
        raise HTTPException(404, f"未找到测试包 {safe}.apk（请放到服务器 {base}/）")
    return FileResponse(path, filename=f"{safe}.apk", media_type="application/vnd.android.package-archive")


@router.get("/app-packages")
async def list_app_packages(app: str):
    """某个 app 端可选的测试包版本（执行弹框「更换测试包」下拉数据源）。

    真实的包版本查询接口待外部提供，现由 services/app_packages 返回内置测试项打通链路。
    """
    from app.services.app_packages import list_packages
    return {"app": app, "packages": list_packages(app)}


@router.get("/devices")
async def list_connected_devices(db: AsyncSession = Depends(get_db)):
    """列出可用于 App 执行的真机。

    执行机 worker 模型：真机由执行机上的 worker 心跳上报到 mobile_devices，这里返回在线设备，
    供执行配置选目标设备 / 判断是否有可用真机。（不再依赖平台所在机器的本地 adb。）
    """
    from app.models import MobileDevice, AppExecJob
    from app.services.data_scope import admin_user_ids
    from datetime import datetime, timedelta
    # 心跳新鲜度：worker 每 ~10s 心跳一次；超过 60s 没心跳视为离线(worker 停了 online 不会自动改)
    cutoff = datetime.now() - timedelta(seconds=60)
    rows = (await db.execute(
        select(MobileDevice).where(
            MobileDevice.online == True,  # noqa: E712
            MobileDevice.last_seen >= cutoff,
        ).order_by(MobileDevice.is_shared.desc(), MobileDevice.worker_name)
    )).scalars().all()
    # 公共设备口径：worker 显式设 is_shared，或设备归属管理员账号(admin 关联的手机=公共测试机)。
    admin_ids = await admin_user_ids(db)
    devices = [
        {"serial": d.serial, "model": d.model or d.serial, "worker_id": d.worker_id,
         "worker_name": d.worker_name, "is_shared": d.is_shared, "busy": d.busy,
         "owner_user_id": d.owner_user_id, "source": "local",
         "is_public": bool(d.is_shared or (d.owner_user_id and str(d.owner_user_id) in admin_ids))}
        for d in rows
    ]
    # App 侧待执行/执行中的用例数(供「使用公共设备」时提醒排队时长)
    app_queue = (await db.execute(
        select(func.count()).select_from(AppExecJob).where(
            AppExecJob.status.in_(("pending", "claimed", "running"))
        )
    )).scalar() or 0

    # 远程真机(Sonic)：实时查询在线安卓设备，序号编码为 "sonic:<udId>" 直接作目标设备。
    # 失败不阻断本地设备返回(Sonic 不可用时仍能用本地/公共)。
    sonic_devices: list[dict] = []
    sonic_error = None
    if settings.sonic_enabled:
        try:
            from app.services.sonic_client import SonicClient
            for d in await SonicClient().list_android_devices():
                if not d.get("online"):
                    continue
                sonic_devices.append({
                    "serial": f"sonic:{d['udId']}", "model": d["model"], "source": "sonic",
                    "busy": bool(d.get("occupied_by")), "occupied_by": d.get("occupied_by") or None,
                    "is_public": True,  # 远程真机对所有人可用（公共池）
                })
        except Exception as e:
            sonic_error = str(e)
            logger.warning("拉取 Sonic 设备失败：%s", e)

    return {"adb_available": True, "devices": devices, "sonic_devices": sonic_devices,
            "sonic_enabled": settings.sonic_enabled, "sonic_error": sonic_error,
            "app_queue": int(app_queue), "error": None}


@router.get("/web-accounts")
async def list_web_accounts(platforms: str = ""):
    """列出给定 PC 端在自动化框架里已配的账号(只读)，供执行弹框选择/切换账号。

    platforms：逗号分隔的端名(如 web-admin,web-portal)。返回 {端: [{role,label}]}。
    框架未覆盖的端返回空列表(前端则只提供「临时账号」输入)。
    """
    from app.services.web_login import account_meta
    result: dict[str, dict] = {}
    for p in [s.strip() for s in platforms.split(",") if s.strip()]:
        result[p] = account_meta(p)
    return result


@router.get("/shots/{name}")
async def get_execution_shot(name: str):
    """返回 App 真机执行过程/结果截图(uploads/exec_shots/{name})。"""
    from fastapi.responses import FileResponse
    from pathlib import Path
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    fp = Path(__file__).resolve().parents[2] / "uploads" / "exec_shots" / name
    if not fp.exists():
        raise HTTPException(404, "截图不存在")
    return FileResponse(str(fp))


@router.get("", response_model=list[ExecutionOut])
async def list_executions(
    project_id: str | None = None,
    requirement_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Execution).order_by(Execution.created_at.desc())
    if project_id:
        q = q.where(Execution.project_id == project_id)
    if requirement_id:
        subq = (
            select(TestResult.execution_id)
            .join(TestCase, TestResult.test_case_id == TestCase.id)
            .where(TestCase.requirement_id == requirement_id)
            .distinct()
        )
        q = q.where(Execution.id.in_(subq))
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/requirement-overview")
async def requirement_execution_overview(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """按需求聚合的执行概览（替代「执行历史」台账，消除与需求详情的重复）。

    每个需求一行：用例数、执行次数（涉及该需求用例的去重 execution 数）、
    最近一次执行的统计（时间/通过率/各状态数/门禁）。一次执行可能横跨多个需求，
    通过 TestResult→TestCase→requirement_id 间接关联，故同一执行会计入它涉及的每个需求。
    """
    # 1) 需求列表（按项目过滤）
    req_q = select(Requirement).order_by(Requirement.created_at.desc())
    if project_id:
        req_q = req_q.where(Requirement.project_id == project_id)
    requirements = (await db.execute(req_q)).scalars().all()
    if not requirements:
        return []
    req_ids = [r.id for r in requirements]

    # 2) 每个需求的用例数
    case_rows = (await db.execute(
        select(TestCase.requirement_id, func.count(TestCase.id))
        .where(TestCase.requirement_id.in_(req_ids))
        .group_by(TestCase.requirement_id)
    )).all()
    case_count = {rid: cnt for rid, cnt in case_rows}

    # 3) 需求 → 涉及的执行（去重），附带执行的统计字段，便于一次算出"次数"与"最近一次"
    exec_rows = (await db.execute(
        select(
            TestCase.requirement_id,
            Execution.id,
            Execution.created_at,
            Execution.pass_rate,
            Execution.passed,
            Execution.failed,
            Execution.skipped,
            Execution.total,
            Execution.status,
            Execution.ci_gate_result,
        )
        .select_from(TestResult)
        .join(TestCase, TestResult.test_case_id == TestCase.id)
        .join(Execution, TestResult.execution_id == Execution.id)
        .where(TestCase.requirement_id.in_(req_ids))
        .distinct()
    )).all()

    # 在 Python 聚合：每个需求的去重执行集合 + 最近一次执行
    agg: dict[str, dict] = {}
    for row in exec_rows:
        rid = row.requirement_id
        bucket = agg.setdefault(rid, {"exec_ids": set(), "latest": None})
        bucket["exec_ids"].add(row.id)
        latest = bucket["latest"]
        if latest is None or (row.created_at and row.created_at > latest.created_at):
            bucket["latest"] = row

    overview = []
    for r in requirements:
        bucket = agg.get(r.id)
        latest = bucket["latest"] if bucket else None
        overview.append({
            "requirement_id": r.id,
            "title": r.title,
            "product_line": r.product_line,
            "status": r.status,
            "case_count": case_count.get(r.id, 0),
            "execution_count": len(bucket["exec_ids"]) if bucket else 0,
            "last_execution": None if latest is None else {
                "execution_id": latest.id,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
                "status": latest.status,
                "pass_rate": latest.pass_rate,
                "passed": latest.passed,
                "failed": latest.failed,
                "skipped": latest.skipped,
                "total": latest.total,
                "ci_gate_result": latest.ci_gate_result,
            },
        })
    return overview


@router.post("", response_model=ExecutionOut, status_code=201)
async def create_execution(
    body: ExecutionCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        ai_key = await resolve_user_ai_key(db, current_user)
    except NoAiKeyError as e:
        raise HTTPException(400, str(e))
    if body.case_ids:
        case_ids = body.case_ids
    else:
        q = select(TestCase.id).where(TestCase.project_id == body.project_id)
        if body.requirement_id:
            q = q.where(TestCase.requirement_id == body.requirement_id)
        result = await db.execute(q)
        case_ids = list(result.scalars().all())

    if not case_ids:
        raise HTTPException(400, "没有可执行的用例，请先生成用例或在列表中选择要执行的用例")

    execution = Execution(
        project_id=body.project_id,
        name=body.name,
        trigger=body.trigger,
        status="pending",
        total=len(case_ids),
    )
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    # 执行模式分流（详设 P1）：
    # - real：入 RQ 队列，由独立执行机(worker)真实执行（接口走 ApiRunner，未就绪端回退 Mock）
    # - mock：保留原 BackgroundTasks 假执行，保证无队列/worker 环境仍可演示
    if settings.execution_mode == "real":
        # execution_inproc=true：直接在进程内真实执行(免独立 RQ worker，App 真机执行走这里)。
        # 优先级高于 RQ —— 否则 enqueue 成功但无 worker 消费会卡死(任务永远 pending)。
        if settings.execution_inproc:
            from app.services.execution_runner import run_execution
            background_tasks.add_task(run_execution, execution.id, case_ids, body.run_mode, body.account_overrides, body.reorder, ai_key, body.target_device, body.env, body.package_overrides)
            return execution
        try:
            from app.services.queue import enqueue_execution
            enqueue_execution(execution.id, case_ids, body.run_mode, body.account_overrides, body.reorder, ai_key, body.target_device, body.env, body.package_overrides)
        except Exception as e:
            # 队列不可用（Redis 未起/worker 缺失）：
            if settings.mock_allowed:
                logger.warning(
                    "execution %s: real 模式入队失败(%s)，本地降级为 mock 执行。", execution.id, e,
                )
                background_tasks.add_task(MockExecutionRunner().run, execution.id, case_ids, body.run_mode)
            else:
                execution.status = "failed"
                await db.commit()
                raise HTTPException(
                    503,
                    f"执行调度失败：任务队列不可用（请检查 Redis {settings.task_queue_url} 与执行机 worker）：{e}",
                )
    elif settings.mock_allowed:
        background_tasks.add_task(MockExecutionRunner().run, execution.id, case_ids, body.run_mode)
    else:
        # 服务器真实环境但 execution_mode 不是 real：拒绝 mock 执行
        execution.status = "failed"
        await db.commit()
        raise HTTPException(503, "执行未启用真实执行引擎(execution_mode=real)，服务器环境禁止 mock 执行")
    return execution


@router.get("/{exec_id}", response_model=ExecutionOut)
async def get_execution(exec_id: str, db: AsyncSession = Depends(get_db)):
    ex = await db.get(Execution, exec_id)
    if not ex:
        raise HTTPException(404, "Execution not found")
    return ex


@router.get("/{exec_id}/results", response_model=list[TestResultOut])
async def get_results(exec_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TestResult).where(TestResult.execution_id == exec_id)
    )
    return result.scalars().all()


@router.patch("/results/{result_id}/defect", response_model=TestResultOut)
async def update_defect_status(
    result_id: str,
    body: DefectReviewUpdate,
    db: AsyncSession = Depends(get_db),
):
    tr = await db.get(TestResult, result_id)
    if not tr:
        raise HTTPException(404, "Result not found")
    tr.defect_status = body.defect_status
    await db.commit()
    await db.refresh(tr)
    return tr
