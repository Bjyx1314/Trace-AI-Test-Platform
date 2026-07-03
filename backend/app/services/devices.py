"""探测本地连接的安卓真机(adb devices)。移动端直连测试用，不依赖 Appium。"""
from __future__ import annotations
import os
import shutil
import subprocess


def _resolve_adb() -> str | None:
    """解析 adb 可执行文件：配置 > PATH > 常见 SDK 位置。"""
    from app.config import settings
    cands: list[str] = []
    if getattr(settings, "adb_path", None):
        cands.append(settings.adb_path)  # type: ignore[arg-type]
    which = shutil.which("adb")
    if which:
        cands.append(which)
    exe = "adb.exe" if os.name == "nt" else "adb"
    for base in (
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),
    ):
        if base:
            cands.append(os.path.join(base, "platform-tools", exe))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return which  # 可能为 None


def list_devices() -> dict:
    """返回 {adb_available, devices:[{serial,model,status}], error}。status=device 为就绪可用。"""
    adb = _resolve_adb()
    if not adb:
        return {"adb_available": False, "devices": [],
                "error": "未找到 adb，请安装 Android platform-tools 或在配置中设置 ADB_PATH"}
    try:
        out = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=15)
    except Exception as e:
        return {"adb_available": True, "devices": [], "error": f"adb 执行失败: {e}"}

    devices: list[dict] = []
    for line in out.stdout.splitlines()[1:]:  # 跳过表头 "List of devices attached"
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, status = parts[0], parts[1]
        if status != "device":  # offline / unauthorized 等不算就绪
            continue
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
        devices.append({"serial": serial, "model": (model or serial).replace("_", " "), "status": status})
    return {"adb_available": True, "devices": devices, "error": None}
