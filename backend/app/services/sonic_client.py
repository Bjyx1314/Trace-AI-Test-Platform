"""Sonic 云真机平台客户端 —— 供后端程序化 列设备 / 占用取远程adb / 释放。

接口契约(源码确证，见 SonicCloudOrg/sonic-server)：
- 基址：settings.sonic_base_url，形如 http://host:3000/api/controller
- 鉴权头：SonicToken；登录 POST /users/login {userName,password} → data=JWT
- 设备列表 GET /devices/listAll?platform=1(安卓)；字段 udId/model/status(ONLINE可用)/user(占用者,空=空闲)/agentId
- 占用 POST /devices/occupy {udId, sasRemotePort} → data.sas = "adb connect <agentHost>:<port>"
- 释放 GET /devices/release?udId=...(仅占用者)

占用是 REST(无需长连接心跳)，agent 侧默认 480 分钟自动释放，我们用完即主动 release。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 全局登记「当前平台占用中的 Sonic 真机 udId」。占用时登记、释放时移除。
# 用途：进程优雅关闭(部署/重启发 SIGTERM)时统一释放，避免执行被打断后 finally 未执行、真机卡在 DEBUGGING。
_ACTIVE_OCCUPIED: set[str] = set()


async def release_all_occupied() -> int:
    """释放所有登记在案的占用（优雅关闭钩子调用，best-effort）。返回释放台数。"""
    uds = list(_ACTIVE_OCCUPIED)
    if not uds:
        return 0
    try:
        cli = SonicClient()
    except Exception:
        return 0
    n = 0
    for ud in uds:
        try:
            await cli.release(ud)
            n += 1
            logger.info("优雅关闭：释放残留占用的 Sonic 真机 %s", ud)
        except Exception as e:  # noqa: BLE001
            logger.warning("优雅关闭释放 %s 失败：%s", ud, e)
        _ACTIVE_OCCUPIED.discard(ud)
    return n


async def release_self_stale() -> int:
    """启动回收：释放【本平台账号占用、且卡在使用中(非 ONLINE/离线)】的真机。
    进程刚启动、无执行在跑，这类必是上次崩溃/被部署打断留下的泄漏占用，直接放掉。返回释放台数。"""
    self_user = (settings.sonic_username or "").strip()
    if not self_user:
        return 0
    try:
        cli = SonicClient()
        devs = await cli.list_android_devices()
    except Exception:
        return 0
    n = 0
    for d in devs:
        occ = (d.get("occupied_by") or "").strip()
        st = (d.get("status") or "").upper()
        if occ == self_user and st not in ("ONLINE", "", "DISCONNECTED", "OFFLINE"):
            try:
                await cli.release(d.get("udId"))
                n += 1
                logger.info("启动回收：释放残留占用的 Sonic 真机 %s (status=%s)", d.get("udId"), st)
            except Exception as e:  # noqa: BLE001
                logger.warning("启动回收释放 %s 失败：%s", d.get("udId"), e)
    return n


class SonicError(RuntimeError):
    pass


class SonicClient:
    def __init__(self) -> None:
        base = (settings.sonic_base_url or "").rstrip("/")
        if not base:
            raise SonicError("未配置 sonic_base_url")
        self._base = base
        self._token: str | None = settings.sonic_token or None  # 配了静态 token 则直接用

    async def _login(self, client: httpx.AsyncClient) -> str:
        if not settings.sonic_username or not settings.sonic_password:
            raise SonicError("未配置 Sonic 账号密码，也无静态 token")
        r = await client.post(f"{self._base}/users/login",
                              json={"userName": settings.sonic_username, "password": settings.sonic_password},
                              timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        token = data.get("data")
        if not token:
            raise SonicError(f"Sonic 登录失败：{data.get('message') or data}")
        return token

    async def _headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        if not self._token:
            self._token = await self._login(client)
        return {"SonicToken": self._token}

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> Any:
        """GET，带 401/token 失效重登一次。"""
        for attempt in (1, 2):
            r = await client.get(f"{self._base}{path}", headers=await self._headers(client), params=params, timeout=20)
            if r.status_code in (401, 403) and attempt == 1 and not settings.sonic_token:
                self._token = None  # 失效 → 重登重试
                continue
            r.raise_for_status()
            return r.json()
        raise SonicError(f"Sonic GET {path} 鉴权失败")

    async def list_android_devices(self) -> list[dict]:
        """返回安卓设备精简列表：[{udId, model, status, occupied_by, online, agent_id}]。"""
        async with httpx.AsyncClient() as client:
            data = await self._get(client, "/devices/listAll", platform=1)
        rows = (data or {}).get("data") or []
        out = []
        for d in rows:
            status = d.get("status") or ""
            out.append({
                "udId": d.get("udId"),
                "model": d.get("model") or d.get("name") or d.get("udId"),
                "status": status,
                "occupied_by": (d.get("user") or "").strip(),
                "online": status == "ONLINE",   # 只有 ONLINE 可占用
                "agent_id": d.get("agentId"),
            })
        return out

    async def occupy(self, ud_id: str, sas_port: int) -> str:
        """占用设备并开远程 adb，返回 "ip:port"（可直接 adb connect）。"""
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{self._base}/devices/occupy",
                                  headers=await self._headers(client),
                                  json={"udId": ud_id, "sasRemotePort": sas_port}, timeout=30)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
        sas = data.get("sas") or ""
        # sas 形如 "adb connect 127.0.0.1:30000"；取末尾 ip:port
        endpoint = sas.replace("adb connect", "").strip()
        if not endpoint or ":" not in endpoint:
            raise SonicError(f"Sonic 占用 {ud_id} 未返回有效 adb 端点：{data}")
        _ACTIVE_OCCUPIED.add(ud_id)  # 登记占用，供优雅关闭统一释放
        return endpoint

    async def release(self, ud_id: str) -> None:
        """释放设备（best-effort，不抛，失败仅日志——agent 侧仍会超时自动释放）。"""
        _ACTIVE_OCCUPIED.discard(ud_id)  # 无论释放成败都撤销登记，避免关闭时重复释放
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{self._base}/devices/release",
                                 headers=await self._headers(client), params={"udId": ud_id}, timeout=15)
        except Exception as e:
            logger.warning("Sonic 释放设备 %s 失败(将由 agent 超时自动释放)：%s", ud_id, e)
