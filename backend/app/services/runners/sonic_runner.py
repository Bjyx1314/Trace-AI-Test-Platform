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
_SONIC_CACHE_KEY = "_sonic_sessions"


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


async def _connect_endpoint(endpoint: str) -> str | None:
    """确保 Sonic 暴露的远程 adb 端点已就绪；返回 None 表示成功，否则返回最后错误。"""
    last_out = ""
    for _ in range(5):
        cp = await asyncio.to_thread(_adb, ["connect", endpoint])
        out = (cp.stdout or "").lower()
        if "connected" in out or "already" in out:
            break
        last_out = cp.stdout or cp.stderr or ""
        await asyncio.sleep(1.5)
    else:
        return f"adb 连接 Sonic 远程真机失败({endpoint})，已重试5次：{last_out}"

    for _ in range(6):
        cp = await asyncio.to_thread(_adb, ["-s", endpoint, "shell", "echo", "ready"])
        out = (cp.stdout or "") + (cp.stderr or "")
        if "ready" in out:
            return None
        last_out = out
        await asyncio.sleep(1.5)
    return f"Sonic 远程真机连接后 shell 预热失败({endpoint})，链路不稳定：{last_out}"


async def release_cached_sonic_sessions(extra: dict | None) -> None:
    sessions = (extra or {}).get(_SONIC_CACHE_KEY) if isinstance(extra, dict) else None
    if not isinstance(sessions, dict):
        return
    for sess in list(sessions.values()):
        endpoint = sess.get("endpoint")
        ud_id = sess.get("ud_id")
        client = sess.get("client")
        if endpoint:
            try:
                await asyncio.to_thread(_adb, ["disconnect", endpoint], 15)
            except Exception:
                pass
        if client and ud_id:
            try:
                await client.release(ud_id)
            except Exception:
                pass
    sessions.clear()


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

        sessions = ctx.extra.setdefault(_SONIC_CACHE_KEY, {}) if isinstance(ctx.extra, dict) else {}
        cached = sessions.get(target) if isinstance(sessions, dict) else None
        endpoint: str | None = cached.get("endpoint") if isinstance(cached, dict) else None
        if not endpoint:
            sas_port = _pick_sas_port()
            try:
                endpoint = await client.occupy(ud_id, sas_port)
            except Exception as e:
                return RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                  error_message=f"占用 Sonic 远程真机({ud_id})失败：{e}")
            if isinstance(sessions, dict):
                sessions[target] = {"client": client, "ud_id": ud_id, "endpoint": endpoint}

        try:
            err = await _connect_endpoint(endpoint)
            if err:
                return RunOutcome(status="error", duration_ms=0, failure_type="env_error", error_message=err)

            ctx.device_udid = endpoint  # AndroidAgentRunner 用它连真机
            return await AndroidAgentRunner().run(case, ctx)
        finally:
            # 同一批次同一台 Sonic 真机复用同一占用/adb 端点，统一在批次收尾时释放。
            pass

    def _prepare(self, case: Any, ctx: RunContext):
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError
