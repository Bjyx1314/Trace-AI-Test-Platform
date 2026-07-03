"""执行机 worker 对接接口（App 真机执行机模型）。

worker 跑在插真机的执行机上，通过这些接口与平台配合：
- POST /api/worker/heartbeat       上报身份+所连设备（保活、上线/下线、忙闲来源）
- POST /api/worker/claim           领取一条派发给本机设备的 App 任务（含兜底默认设备）
- POST /api/worker/jobs/{id}/result 回传执行结果（写回任务，释放设备）
- POST /api/worker/jobs/{id}/shot   上传执行截图，返回可访问 URL
- GET  /api/worker/devices         列出在线设备（供执行配置选目标设备）

鉴权：Header `Authorization: Bearer <WORKER_TOKEN>`；未配置 worker_token 时不校验（仅本地）。
"""
from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import MobileDevice, AppExecJob

router = APIRouter(prefix="/api/worker", tags=["worker"])

_SHOTS_DIR = Path(__file__).resolve().parents[2] / "uploads" / "exec_shots"


# ── 「连接我的真机」：下载 worker 程序 + 安装信息 ────────────────────────────
def _artifact_for(os_name: str | None) -> tuple[Path, str]:
    """按客户端系统返回 (worker 产物路径, 下载文件名)。默认 windows。"""
    if (os_name or "").lower() in ("mac", "macos", "darwin", "osx"):
        return Path(settings.worker_exe_path_mac), "tp-worker"
    return Path(settings.worker_exe_path), "tp-worker.exe"


@router.get("/install-info")
async def install_info(os: str | None = None, current_user: dict = Depends(get_current_user)):
    """供「连接我的真机」页生成下载/运行指引。返回对应系统产物是否就绪 + 连接令牌 + 当前用户 id(设备归属)。"""
    win_path = Path(settings.worker_exe_path)
    mac_path = Path(settings.worker_exe_path_mac)
    target, _ = _artifact_for(os)
    return {
        "exe_available": target.exists(),          # 请求系统对应产物是否就绪
        "win_available": win_path.exists(),
        "mac_available": mac_path.exists(),
        "worker_token": settings.worker_token or "",
        "owner_user_id": (current_user or {}).get("uid") or (current_user or {}).get("sub") or "",
    }


@router.get("/download")
async def download_worker(os: str | None = None):
    """下载执行机 worker 程序（含 Python+依赖+adb，开箱即用）。os=mac 下 mac 原生二进制，否则 Windows exe。"""
    p, filename = _artifact_for(os)
    if not p.exists():
        raise HTTPException(404, f"{filename} 尚未上传，请联系管理员构建并放到服务器（{p}）")
    return FileResponse(str(p), filename=filename, media_type="application/octet-stream")


async def require_worker(authorization: str | None = Header(default=None)) -> None:
    """校验 worker 令牌。未配置 worker_token 则放行（本地）。"""
    if not settings.worker_token:
        return
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token != settings.worker_token:
        raise HTTPException(401, "worker 令牌无效")


# ── 心跳 / 设备上报 ──────────────────────────────────────────────────────────
class DeviceIn(BaseModel):
    serial: str
    model: str | None = None


class HeartbeatIn(BaseModel):
    worker_id: str
    worker_name: str | None = None
    is_shared: bool = False          # 本机设备是否作为公共/默认设备（无 worker 的人兜底走它）
    owner_user_id: str | None = None  # 本机设备归属用户（空=公共）
    devices: list[DeviceIn] = []


@router.post("/heartbeat", dependencies=[Depends(require_worker)])
async def heartbeat(body: HeartbeatIn, db: AsyncSession = Depends(get_db)):
    now = datetime.now()
    seen_serials = set()
    for d in body.devices:
        seen_serials.add(d.serial)
        existing = (await db.execute(
            select(MobileDevice).where(
                MobileDevice.worker_id == body.worker_id,
                MobileDevice.serial == d.serial,
            )
        )).scalar_one_or_none()
        if existing:
            existing.model = d.model or existing.model
            existing.worker_name = body.worker_name or existing.worker_name
            existing.is_shared = body.is_shared
            existing.owner_user_id = body.owner_user_id
            existing.online = True
            existing.last_seen = now
        else:
            db.add(MobileDevice(
                worker_id=body.worker_id, worker_name=body.worker_name,
                serial=d.serial, model=d.model, is_shared=body.is_shared,
                owner_user_id=body.owner_user_id, online=True, busy=False, last_seen=now,
            ))
    # 本 worker 名下、本次心跳未上报的设备 → 标记离线（拔线/掉了）
    await db.execute(
        update(MobileDevice)
        .where(MobileDevice.worker_id == body.worker_id, MobileDevice.serial.notin_(seen_serials or [""]))
        .values(online=False)
    )
    await db.commit()
    return {"ok": True, "online_devices": len(seen_serials)}


# ── 领取任务 ────────────────────────────────────────────────────────────────
@router.post("/claim", dependencies=[Depends(require_worker)])
async def claim(worker_id: str, db: AsyncSession = Depends(get_db)):
    """领取一条可在本机执行的任务。优先定向到本机设备的任务，其次本机公共设备的兜底任务。"""
    # 本 worker 当前在线、空闲的设备
    my_devs = (await db.execute(
        select(MobileDevice).where(
            MobileDevice.worker_id == worker_id,
            MobileDevice.online == True,  # noqa: E712
            MobileDevice.busy == False,   # noqa: E712
        )
    )).scalars().all()
    if not my_devs:
        return {"job": None}
    idle_serials = [d.serial for d in my_devs]
    shared_idle = [d.serial for d in my_devs if d.is_shared]

    async def _take(job_query) -> AppExecJob | None:
        job = (await db.execute(job_query.with_for_update(skip_locked=True))).scalars().first()
        return job

    # 1) 定向到本机设备的任务
    job = await _take(
        select(AppExecJob).where(
            AppExecJob.status == "pending",
            AppExecJob.target_serial.in_(idle_serials),
        ).order_by(AppExecJob.created_at).limit(1)
    )
    chosen_serial = job.target_serial if job else None
    # 2) 兜底：未定向任务 + 本机有公共空闲设备
    if job is None and shared_idle:
        job = await _take(
            select(AppExecJob).where(
                AppExecJob.status == "pending",
                AppExecJob.target_serial.is_(None),
            ).order_by(AppExecJob.created_at).limit(1)
        )
        chosen_serial = shared_idle[0] if job else None

    if job is None:
        return {"job": None}

    job.status = "claimed"
    job.claimed_worker = worker_id
    job.claimed_serial = chosen_serial
    job.updated_at = datetime.now()
    # 占用该设备
    dev = next((d for d in my_devs if d.serial == chosen_serial), None)
    if dev:
        dev.busy = True
    await db.commit()
    # 注：AI 配置由各执行机 worker 自行配置（各用各的本地 AI_*），平台不下发统一 AI 配置。
    return {"job": {"id": job.id, "serial": chosen_serial, "payload": job.payload or {}}}


# ── 回传结果 ────────────────────────────────────────────────────────────────
class ResultIn(BaseModel):
    status: str                       # passed/failed/skipped/error
    duration_ms: int = 0
    error_message: str | None = None
    failure_type: str | None = None   # script_error/env_error/real_defect
    ui_trace: list | None = None


@router.post("/jobs/{job_id}/result", dependencies=[Depends(require_worker)])
async def post_result(job_id: str, body: ResultIn, db: AsyncSession = Depends(get_db)):
    job = await db.get(AppExecJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    job.result = body.model_dump()
    job.status = "done"
    job.finished_at = datetime.now()
    job.updated_at = datetime.now()
    # 释放设备
    if job.claimed_serial:
        await db.execute(
            update(MobileDevice)
            .where(MobileDevice.serial == job.claimed_serial, MobileDevice.worker_id == job.claimed_worker)
            .values(busy=False)
        )
    await db.commit()
    return {"ok": True}


# ── 上传截图 ────────────────────────────────────────────────────────────────
class ShotIn(BaseModel):
    name: str       # 文件名（worker 生成，如 {exec}_{case}_{idx}.jpg）
    b64: str        # JPEG base64


@router.post("/jobs/{job_id}/shot", dependencies=[Depends(require_worker)])
async def upload_shot(job_id: str, body: ShotIn, db: AsyncSession = Depends(get_db)):
    name = re.sub(r"[^0-9A-Za-z_.\-]+", "_", body.name)[:120]
    if not name:
        raise HTTPException(400, "非法文件名")
    try:
        data = base64.b64decode(body.b64)
    except Exception:
        raise HTTPException(400, "截图数据无效")
    _SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    (_SHOTS_DIR / name).write_bytes(data)
    return {"url": f"/api/executions/shots/{name}"}


# ── 设备列表（供执行配置选目标设备）──────────────────────────────────────────
@router.get("/devices")
async def list_worker_devices(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(MobileDevice).where(MobileDevice.online == True)  # noqa: E712
        .order_by(MobileDevice.is_shared.desc(), MobileDevice.worker_name)
    )).scalars().all()
    return {
        "devices": [
            {"serial": d.serial, "model": d.model or d.serial, "worker_id": d.worker_id,
             "worker_name": d.worker_name, "is_shared": d.is_shared, "busy": d.busy}
            for d in rows
        ]
    }
