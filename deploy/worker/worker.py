#!/usr/bin/env python
"""App 真机执行机 worker（拉取式，开箱即用）。

跑在【插着安卓真机的执行机】上：主动连平台心跳上报设备 → 领取派发到本机的 App 任务 →
用平台现成的 AndroidAgentRunner 本地连真机执行（AI 视觉，全程本地、USB 直连、稳）→
上传截图 + 回传结果。这是未来 Sonic-agent 的骨架。

零配置理念：启动时自动读取本机 backend/.env（复用平台的 AI 配置 AI_*、WORKER_TOKEN 等），
WORKER_ID 默认取机器名，PLATFORM_URL 默认指本机开发服务。

可选环境变量（不设则按上面默认/从 .env 自动取）：
  PLATFORM_URL   平台地址（默认 http://localhost:8000）
  WORKER_ID      执行机标识（默认=机器名 hostname）
  WORKER_NAME    显示名（默认=WORKER_ID）
  WORKER_TOKEN   平台 worker 令牌（默认从 backend/.env 读）
  WORKER_SHARED  true=本机设备作公共/默认设备（默认 false）
  AI_PROVIDER / AI_API_KEY / AI_BASE_URL / AI_MODEL  默认从 backend/.env 自动取
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_env_file(p: Path) -> int:
    """把 backend/.env 的 KEY=VALUE 注入进程环境（不覆盖已显式设置的）。
    让 worker 自动复用本机平台的 AI 配置(AI_*)与 WORKER_TOKEN，使用者无需手配。"""
    if not p.exists():
        return 0
    n = 0
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


_loaded = _load_env_file(_BACKEND / ".env")


def _cfg_path() -> Path:
    """配置文件放 exe 同目录(打包)或脚本目录。首次用命令跑会写入，之后双击即可复用。"""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return base / "tp-worker.json"


def _load_cfg_file() -> None:
    p = _cfg_path()
    if not p.exists():
        return
    try:
        import json
        for k, v in (json.loads(p.read_text(encoding="utf-8")) or {}).items():
            if v and k not in os.environ:
                os.environ[k] = str(v)
    except Exception:
        pass


def _save_cfg_file() -> None:
    try:
        import json
        _cfg_path().write_text(json.dumps({
            "PLATFORM_URL": PLATFORM, "WORKER_TOKEN": TOKEN, "WORKER_OWNER": OWNER,
            "WORKER_SHARED": "true" if IS_SHARED else "false", "WORKER_NAME": WORKER_NAME,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _install_autostart() -> None:
    """注册开机自启：Windows 走 HKCU Run(免管理员)，mac 走用户级 LaunchAgent；
    之后开机自动上线、不用再管。仅对打包后的可执行文件生效(脚本运行不注册)。"""
    if not getattr(sys, "frozen", False):
        return
    if os.name == "nt":
        _autostart_windows()
    elif sys.platform == "darwin":
        _autostart_macos()


def _autostart_windows() -> None:
    try:
        import winreg
        exe = sys.executable
        cmd = f'cmd /c start "" /min "{exe}"'  # 最小化启动，不干扰
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "tp-worker", 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        print("[autostart] 已设为开机自启：以后开机自动上线，无需再手动启动。")
    except Exception as e:
        print(f"[autostart] 设置开机自启失败(可忽略，仍可手动双击启动): {e}")


def _autostart_macos() -> None:
    """写入用户级 LaunchAgent(~/Library/LaunchAgents/com.tp.worker.plist)并加载，
    登录后由 launchd 自动拉起、崩溃自动重启，无需常开终端窗口。"""
    import subprocess
    label = "com.tp.worker"
    exe = sys.executable
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = agents_dir / f"{label}.plist"
    # 日志落到 exe 同目录，便于排查(launchd 下无终端)
    log_dir = Path(exe).parent
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{label}</string>\n'
        '  <key>ProgramArguments</key>\n'
        f'  <array><string>{exe}</string></array>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '  <key>KeepAlive</key><true/>\n'
        f'  <key>StandardOutPath</key><string>{log_dir / "tp-worker.log"}</string>\n'
        f'  <key>StandardErrorPath</key><string>{log_dir / "tp-worker.err.log"}</string>\n'
        '</dict></plist>\n'
    )
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist, encoding="utf-8")
        # 先卸后装，避免重复加载报错；bootout/bootstrap 失败静默(老系统回退 load)
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
                       capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
        print("[autostart] 已设为开机自启(LaunchAgent)：以后登录后自动上线，无需再手动启动。")
    except Exception as e:
        print(f"[autostart] 设置开机自启失败(可忽略，仍可手动启动): {e}")


_load_cfg_file()  # 双击时从 exe 旁的 tp-worker.json 复用上次配置

# 注入 .env 后再导入平台代码：settings 会读到上面的 AI_* / WORKER_TOKEN
import httpx  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.devices import list_devices  # noqa: E402
from app.services.runners.android_runner import AndroidAgentRunner, _UPLOADS  # noqa: E402
from app.services.runners.base import RunContext  # noqa: E402

PLATFORM = (os.environ.get("PLATFORM_URL") or "http://localhost:8000").rstrip("/")
WORKER_ID = os.environ.get("WORKER_ID") or socket.gethostname() or "worker-1"
WORKER_NAME = os.environ.get("WORKER_NAME") or WORKER_ID
TOKEN = os.environ.get("WORKER_TOKEN") or settings.worker_token or ""
IS_SHARED = (os.environ.get("WORKER_SHARED") or "false").lower() == "true"
OWNER = os.environ.get("WORKER_OWNER") or ""   # 设备归属用户 id（「连接我的真机」下载命令里带当前登录用户）
POLL_SEC = float(os.environ.get("POLL_SEC") or "3")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

if TOKEN:
    _save_cfg_file()      # 带令牌成功配置即持久化
    _install_autostart()  # 并注册开机自启，之后开机自动上线


def _print_startup():
    print("=" * 56)
    print("App 执行机 worker 启动")
    print(f"  平台      : {PLATFORM}")
    print(f"  worker_id : {WORKER_ID}  (公共默认设备={IS_SHARED})")
    print(f"  令牌      : {'已配' if TOKEN else '未配(平台若开鉴权会 401)'}")
    print("  AI        : 由平台按发起人下发(per-user key)，本机无需配置")
    print("=" * 56)


def _unify_adb(adb_path: str) -> None:
    """让 uiautomator2/adbutils 用同一个 adb，避免起第二个 adb server 版本冲突(卡在 adb)。"""
    from app.config import settings as wsettings
    wsettings.adb_path = adb_path
    # adbutils(uiautomator2 依赖)优先读这两个环境变量来定位 adb
    os.environ["ADBUTILS_ADB_PATH"] = adb_path
    os.environ.setdefault("ANDROID_ADB_SERVER_PORT", "5037")


def ensure_adb() -> None:
    """确保 adb 可用，并统一 uiautomator2/adbutils 用同一个 adb。
    优先级：本机已装 adb > exe 内置 adb。"""
    from app.services.devices import _resolve_adb
    sys_adb = _resolve_adb()
    if sys_adb:
        _unify_adb(sys_adb)
        print(f"[adb] 使用本机 adb：{sys_adb}")
        return
    # 打成单 exe 时内置的 adb（PyInstaller onefile 运行解压目录 _MEIPASS）
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        if bundled.exists():
            _unify_adb(str(bundled))
            print(f"[adb] 使用内置 adb：{bundled}")
            return
    print("[adb] 未找到 adb：exe 版应内置 adb；脚本版请安装 Android platform-tools 或设 ADB_PATH")


async def _heartbeat(client: httpx.AsyncClient):
    devs = (list_devices().get("devices") or [])
    try:
        await client.post(f"{PLATFORM}/api/worker/heartbeat", headers=HEADERS, json={
            "worker_id": WORKER_ID, "worker_name": WORKER_NAME, "is_shared": IS_SHARED,
            "owner_user_id": OWNER or None,
            "devices": [{"serial": d["serial"], "model": d.get("model")} for d in devs],
        }, timeout=15)
    except Exception as e:
        print(f"[heartbeat] 失败: {e}")
    return devs


async def _claim(client: httpx.AsyncClient):
    try:
        r = await client.post(f"{PLATFORM}/api/worker/claim", headers=HEADERS,
                              params={"worker_id": WORKER_ID}, timeout=15)
        if r.status_code == 401:
            print("[claim] 401：WORKER_TOKEN 与平台不一致")
            return None
        return (r.json() or {}).get("job")
    except Exception as e:
        print(f"[claim] 失败: {e}")
        return None


async def _upload_shots(client: httpx.AsyncClient, job_id: str, ui_trace: list):
    import base64
    # ui_trace 每步的截图字段是单数 "shot"(单个路径)；兼容历史的复数 "shots"(列表)
    names = set()
    for step in (ui_trace or []):
        if step.get("shot"):
            names.add(Path(step["shot"]).name)
        for u in (step.get("shots") or []):
            if u:
                names.add(Path(u).name)
    for name in names:
        fp = _UPLOADS / name
        if not fp.exists():
            continue
        try:
            b64 = base64.b64encode(fp.read_bytes()).decode()
            await client.post(f"{PLATFORM}/api/worker/jobs/{job_id}/shot", headers=HEADERS,
                              json={"name": name, "b64": b64}, timeout=30)
        except Exception as e:
            print(f"[shot] 上传 {name} 失败: {e}")


async def _run_job(client: httpx.AsyncClient, job: dict):
    job_id = job["id"]
    serial = job["serial"]
    p = job.get("payload") or {}
    print(f"[job {job_id}] 领到任务 → 设备 {serial}：{p.get('title')}")
    # AI 用平台随任务下发的「发起人 key」+ 中转配置（worker 无需本机 AI 配置）
    from app.config import settings as wsettings
    from app.agents.llm import set_current_ai_key
    if p.get("ai_provider"):
        wsettings.ai_provider = p["ai_provider"]
    if p.get("ai_base_url"):
        wsettings.ai_base_url = p["ai_base_url"]
    if p.get("ai_model") is not None:
        wsettings.ai_model = p["ai_model"]
    set_current_ai_key(p.get("ai_key"))
    if not p.get("ai_key"):
        print(f"[job {job_id}] 警告：任务未带 AI key（发起人可能未分配 key）")

    case = SimpleNamespace(
        id=p.get("case_id"), title=p.get("title") or "",
        steps=p.get("steps") or [], expected_result=p.get("expected_result") or "",
        platforms=p.get("platforms") or [], script=None,
    )
    ctx = RunContext(execution_id=p.get("execution_id") or "exec",
                     base_url=p.get("base_url"), device_udid=serial)
    # App 换测试包(通用、数据驱动)：任务带 apk={source,package} 时，AndroidAgentRunner 执行前会卸旧装新。
    # 换 app/换包/换 apk 都只是任务数据，worker 无需再改再打包。
    if p.get("apk"):
        ctx.extra["apk"] = p.get("apk")
    if p.get("app_package"):
        ctx.extra["app_package"] = p.get("app_package")
    try:
        outcome = await AndroidAgentRunner().run(case, ctx)
    except Exception as e:
        outcome = SimpleNamespace(status="error", duration_ms=0,
                                  error_message=f"worker 执行异常: {e}", failure_type="env_error", ui_trace=None)
    await _upload_shots(client, job_id, getattr(outcome, "ui_trace", None) or [])
    try:
        await client.post(f"{PLATFORM}/api/worker/jobs/{job_id}/result", headers=HEADERS, json={
            "status": outcome.status, "duration_ms": int(getattr(outcome, "duration_ms", 0) or 0),
            "error_message": getattr(outcome, "error_message", None),
            "failure_type": getattr(outcome, "failure_type", None),
            "ui_trace": getattr(outcome, "ui_trace", None),
        }, timeout=30)
        print(f"[job {job_id}] 完成：{outcome.status}")
    except Exception as e:
        print(f"[job {job_id}] 回传结果失败: {e}")


async def main():
    _print_startup()
    if not TOKEN:
        print("\n[配置缺失] 未获取到 WORKER_TOKEN，无法连接平台。")
        print("请回到平台「执行测试配置 → 连接我的真机」，复制其中的 PowerShell 命令，")
        print("在本 exe 所在目录的 PowerShell 里粘贴运行(命令已带平台地址+令牌+归属)。")
        print("首次成功后本目录会生成 tp-worker.json，之后可直接双击本 exe。")
        try:
            input("\n按回车退出…")
        except Exception:
            pass
        return
    ensure_adb()
    last_hb = 0.0
    async with httpx.AsyncClient() as client:
        while True:
            now = time.monotonic()
            if now - last_hb >= 10:
                devs = await _heartbeat(client)
                last_hb = now
                if not devs:
                    print("[warn] adb devices 为空：检查 USB / 调试授权 / adb")
                else:
                    import time as _t
                    print(f"[在线 {_t.strftime('%H:%M:%S')}] 设备 {len(devs)} 台："
                          f"{', '.join(d['serial'] for d in devs)}，等待任务…(平台派 App 任务后会自动执行)")
            job = await _claim(client)
            if job:
                await _run_job(client, job)
                continue
            await asyncio.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nworker 退出")
