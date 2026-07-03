"""WorkerDispatchRunner —— App 真机用例派发给执行机 worker，阻塞等待回传。

平台(服务器)上没有真机，App 用例不在进程内执行，而是建一条 AppExecJob 派发给执行机 worker，
本 Runner 阻塞轮询任务状态，worker 执行完把 RunOutcome 写回任务后，本 Runner 取回返回。
这样执行主循环（建 TestResult/缺陷/收尾）零改动。worker 端用本地 AndroidAgentRunner 真连真机执行。

目标设备路由：ctx.extra['target_device'] 显式指定 > 留空走 worker 端公共/默认设备兜底。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from .base import BaseRunner, RunOutcome, RunContext

_POLL_SEC = 2.0


class WorkerDispatchRunner(BaseRunner):
    platform = "android"
    requires_device = True

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        from sqlalchemy import select, update
        from app.config import settings
        from app.database import AsyncSessionLocal
        from app.models import AppExecJob, MobileDevice

        target = (ctx.extra or {}).get("target_device") or None

        # 建任务前先确认有在线设备，否则直接报错不空等
        async with AsyncSessionLocal() as db:
            if target:
                dev = (await db.execute(
                    select(MobileDevice).where(
                        MobileDevice.serial == target, MobileDevice.online == True)  # noqa: E712
                )).scalar_one_or_none()
                if not dev:
                    return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                      error_message=f"指定真机 {target} 不在线，请确认其执行机 worker 已启动")
            else:
                any_online = (await db.execute(
                    select(MobileDevice).where(MobileDevice.online == True).limit(1)  # noqa: E712
                )).scalar_one_or_none()
                if not any_online:
                    return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                      error_message="无在线执行机/真机：请在插真机的执行机上启动 worker 后重试")

            payload = {
                "execution_id": ctx.execution_id,
                "case_id": getattr(case, "id", None),
                "title": getattr(case, "title", "") or "",
                "steps": getattr(case, "steps", None) or [],
                "expected_result": getattr(case, "expected_result", None) or "",
                "platforms": getattr(case, "platforms", None) or [],
                "base_url": ctx.base_url,
                # 发起人 key + 中转配置：worker 用它执行(用户自己的额度)，worker 无需本机 AI 配置
                "ai_key": (ctx.extra or {}).get("ai_key"),
                "ai_provider": settings.ai_provider,
                "ai_base_url": settings.ai_base_url,
                "ai_model": settings.ai_model,
                # App 换包：{source, package}。worker 领到后执行前卸旧装新(下载/adb 在 worker 侧做)
                "apk": (ctx.extra or {}).get("apk"),
                # App 目标应用包名：worker 执行前按此直接启动 App(不用 AI 桌面找、避免找错 App)
                "app_package": (ctx.extra or {}).get("app_package"),
            }
            job = AppExecJob(
                execution_id=ctx.execution_id,
                test_case_id=getattr(case, "id", None),
                project_id=ctx.project_id,
                target_serial=target,   # None=兜底默认设备
                status="pending",
                payload=payload,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            job_id = job.id

        deadline = float(settings.app_job_timeout_sec)
        waited = 0.0
        while waited < deadline:
            await asyncio.sleep(_POLL_SEC)
            waited += _POLL_SEC
            async with AsyncSessionLocal() as db:
                job = await db.get(AppExecJob, job_id)
                if job and job.status == "done":
                    r = job.result or {}
                    return RunOutcome(
                        status=r.get("status") or "error",
                        duration_ms=int(r.get("duration_ms") or 0),
                        error_message=r.get("error_message"),
                        failure_type=r.get("failure_type"),
                        ui_trace=r.get("ui_trace"),
                    )

        # 超时：收尾任务并释放设备
        async with AsyncSessionLocal() as db:
            job = await db.get(AppExecJob, job_id)
            if job and job.status != "done":
                job.status = "timeout"
                job.finished_at = datetime.now()
                if job.claimed_serial:
                    await db.execute(update(MobileDevice)
                                     .where(MobileDevice.serial == job.claimed_serial)
                                     .values(busy=False))
                await db.commit()
        return RunOutcome(status="error", duration_ms=int(waited * 1000), failure_type="env_error",
                          error_message=f"App 任务等待执行机超时（{int(deadline)}s）：worker 未领取或未按时完成")

    def _prepare(self, case: Any, ctx: RunContext):
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError
