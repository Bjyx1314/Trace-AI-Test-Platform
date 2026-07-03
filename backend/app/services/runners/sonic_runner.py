"""SonicRunner —— 远程真机(Sonic 云真机)执行：占用 → adb connect → 复用 AndroidAgentRunner → 释放。

平台无需本地真机：从 Sonic 占用一台设备拿到远程 adb 端点(ip:port)，adb connect 后当普通网络
adb 设备，交给现有 AndroidAgentRunner(AI 视觉 + uiautomator2) 执行；用完 adb disconnect + 归还 Sonic。
目标设备由 ctx.extra['target_device'] 传入，形如 "sonic:<udId>"。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from .base import BaseRunner, RunOutcome, RunContext
from .android_runner import AndroidAgentRunner

logger = logging.getLogger(__name__)

SONIC_PREFIX = "sonic:"


def _pick_sas_port() -> int:
    """在配置范围内挑一个远程 adb 端口(用完即释放，冲突概率低；随执行序号变化避免同批撞port)。"""
    from app.config import settings
    import time
    lo, hi = settings.sonic_sas_port_min, max(settings.sonic_sas_port_max, settings.sonic_sas_port_min)
    span = hi - lo + 1
    return lo + (int(time.monotonic() * 1000) % span)


def _adb(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    from app.services.devices import _resolve_adb
    adb = _resolve_adb() or "adb"
    return subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout)


class SonicRunner(BaseRunner):
    platform = "android"
    requires_device = True

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        from app.services.sonic_client import SonicClient, SonicError

        target = ((ctx.extra or {}).get("target_device") or "").strip()
        ud_id = target[len(SONIC_PREFIX):] if target.startswith(SONIC_PREFIX) else target
        if not ud_id:
            return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                              error_message="未指定 Sonic 远程真机(udId)")

        try:
            client = SonicClient()
        except SonicError as e:
            return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                              error_message=f"Sonic 未配置：{e}")

        sas_port = _pick_sas_port()
        endpoint: str | None = None
        try:
            endpoint = await client.occupy(ud_id, sas_port)
        except Exception as e:
            return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                              error_message=f"占用 Sonic 远程真机({ud_id})失败：{e}")

        try:
            # adb connect 到 Sonic 暴露的远程端点，随后当普通设备驱动
            cp = await asyncio.to_thread(_adb, ["connect", endpoint])
            if "connected" not in (cp.stdout or "").lower() and "already" not in (cp.stdout or "").lower():
                return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                  error_message=f"adb 连接 Sonic 远程真机失败({endpoint})：{cp.stdout or cp.stderr}")
            ctx.device_udid = endpoint  # AndroidAgentRunner 用它连真机
            return await AndroidAgentRunner().run(case, ctx)
        finally:
            if endpoint:
                try:
                    await asyncio.to_thread(_adb, ["disconnect", endpoint], 15)
                except Exception:
                    pass
            await client.release(ud_id)

    def _prepare(self, case: Any, ctx: RunContext):
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError
