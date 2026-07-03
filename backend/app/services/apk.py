"""APK 装包工具：把指定 apk 推到目标安卓设备上「先卸旧包，再装新包」。

被 AndroidAgentRunner 在执行用例前调用（Sonic 远程 / worker 本地/公共设备 三条路径统一走这里）。
apk 来源(source)支持：
- http(s)://…                → 下载到临时目录再装
- local:<名字>               → 在本机 ~/Downloads 等目录找 <名字>.apk（测试用，随安装机各自本地找）
- 绝对路径                    → 直接用

包名(package)用于卸载旧包：优先用调用方给的(真实接口会返回)，没有则尝试从 apk 解析；
都拿不到就退化为 `adb install -r`(覆盖安装，等效换包，只是不清数据)。

本模块随 worker 打包(tp-worker 内置 backend 代码)，故 worker 与 backend 都能用同一套逻辑。
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _adb_bin() -> str:
    try:
        from app.services.devices import _resolve_adb
        return _resolve_adb() or "adb"
    except Exception:
        return "adb"


def _run_adb(serial: str | None, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    adb = _adb_bin()
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _resolve_source(source: str) -> str | None:
    """把 apk source 解析成本机可读的本地文件路径；下载/查找失败返回 None。"""
    s = (source or "").strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        try:
            import httpx
            dst = Path(tempfile.gettempdir()) / f"tp_apk_{abs(hash(s)) % (10**10)}.apk"
            with httpx.stream("GET", s, timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            return str(dst)
        except Exception as e:
            logger.warning("下载 apk 失败(%s)：%s", s, e)
            return None
    if s.startswith("platform:"):
        # 从平台下载：worker 用其 PLATFORM_URL，后端(Sonic)用本机 8000。apk 集中放平台，各 worker 免拷贝。
        from urllib.parse import quote
        key = s[len("platform:"):].strip()
        base = (os.environ.get("PLATFORM_URL") or "http://localhost:8000").rstrip("/")
        return _resolve_source(f"{base}/api/executions/app-package-file?key={quote(key)}")
    if s.startswith("local:"):
        name = s[len("local:"):].strip()
        cands: list[Path] = []
        for d in (Path.home() / "Downloads", Path.home() / "downloads", Path("/root/Downloads"),
                  Path("/downloads"), Path.cwd() / "downloads"):
            cands += [d / f"{name}.apk", d / name, d / f"{name}.APK"]
        for c in cands:
            if c.is_file():
                return str(c)
        logger.warning("本地 apk 未找到：local:%s（找过 ~/Downloads 等）", name)
        return None
    # 绝对/相对路径
    p = Path(s)
    return str(p) if p.is_file() else None


def _apk_package_name(apk_path: str) -> str | None:
    """尽力从 apk 解析 <manifest package="…">。解析不了返回 None（不影响装新包）。"""
    try:
        with zipfile.ZipFile(apk_path) as z:
            data = z.read("AndroidManifest.xml")
        return _axml_manifest_package(data)
    except Exception as e:
        logger.info("解析 apk 包名失败(%s)：%s", apk_path, e)
        return None


def _axml_manifest_package(data: bytes) -> str | None:
    """极简 AXML 解析：取字符串池 → 找 START_TAG(manifest) 的 package 属性值。"""
    import struct

    if len(data) < 8:
        return None
    # 文件头后第一个 chunk 为字符串池(type 0x0001)
    off = 8
    ctype, _hs, csize = struct.unpack_from("<HHI", data, off)
    if ctype != 0x0001:
        return None
    string_count = struct.unpack_from("<I", data, off + 8)[0]
    flags = struct.unpack_from("<I", data, off + 16)[0]
    strings_start = struct.unpack_from("<I", data, off + 20)[0]
    is_utf8 = bool(flags & (1 << 8))
    off_base = off + 28
    str_base = off + strings_start
    strings: list[str] = []
    for i in range(string_count):
        so = struct.unpack_from("<I", data, off_base + i * 4)[0]
        p = str_base + so
        try:
            if is_utf8:
                # char len(u8 varint) + byte len(u8 varint) + bytes
                def _len8(pp):
                    x = data[pp]; pp += 1
                    if x & 0x80:
                        x = ((x & 0x7F) << 8) | data[pp]; pp += 1
                    return x, pp
                _cl, p = _len8(p)
                bl, p = _len8(p)
                strings.append(data[p:p + bl].decode("utf-8", "replace"))
            else:
                def _len16(pp):
                    x = struct.unpack_from("<H", data, pp)[0]; pp += 2
                    if x & 0x8000:
                        x = ((x & 0x7FFF) << 16) | struct.unpack_from("<H", data, pp)[0]; pp += 2
                    return x, pp
                cl, p = _len16(p)
                strings.append(data[p:p + cl * 2].decode("utf-16-le", "replace"))
        except Exception:
            strings.append("")

    pos = off + csize
    n = len(data)
    while pos + 8 <= n:
        ct, hs, cs = struct.unpack_from("<HHI", data, pos)
        if cs <= 0:
            break
        if ct == 0x0102:  # RES_XML_START_ELEMENT
            ext = pos + hs
            name_idx = struct.unpack_from("<i", data, ext + 4)[0]
            attr_start = struct.unpack_from("<H", data, ext + 8)[0]
            attr_size = struct.unpack_from("<H", data, ext + 10)[0]
            attr_count = struct.unpack_from("<H", data, ext + 12)[0]
            name = strings[name_idx] if 0 <= name_idx < len(strings) else ""
            if name == "manifest":
                abase = ext + attr_start
                for i in range(attr_count):
                    a = abase + i * attr_size
                    a_name = struct.unpack_from("<i", data, a + 4)[0]
                    a_rawval = struct.unpack_from("<i", data, a + 8)[0]
                    a_data = struct.unpack_from("<i", data, a + 16)[0]
                    an = strings[a_name] if 0 <= a_name < len(strings) else ""
                    if an == "package":
                        if 0 <= a_rawval < len(strings) and strings[a_rawval]:
                            return strings[a_rawval]
                        if 0 <= a_data < len(strings) and strings[a_data]:
                            return strings[a_data]
                return None
        pos += cs
    return None


def install_apk(serial: str | None, source: str, package: str | None = None) -> tuple[bool, str]:
    """在设备(serial)上换包：先卸旧包(package)，再装 source 指向的新 apk。

    返回 (成功?, 说明)。source 解析失败/安装失败返回 (False, 原因)。
    """
    local = _resolve_source(source)
    if not local:
        return False, f"未取到 apk 安装包（source={source}）"

    pkg = package or _apk_package_name(local)
    if pkg:
        try:
            cp = _run_adb(serial, ["uninstall", pkg], timeout=120)
            logger.info("卸载旧包 %s：%s", pkg, (cp.stdout or cp.stderr or "").strip()[:200])
        except Exception as e:
            logger.info("卸载旧包 %s 忽略异常：%s", pkg, e)  # 没装过/卸载失败不阻断装新包
    else:
        logger.info("未知 apk 包名，跳过卸载，直接覆盖安装(-r)")

    try:
        cp = _run_adb(serial, ["install", "-r", "-t", local], timeout=600)
    except Exception as e:
        return False, f"adb install 异常：{e}"
    out = f"{cp.stdout or ''}\n{cp.stderr or ''}"
    if "Success" in out:
        return True, f"已安装新包{('（' + pkg + '）') if pkg else ''}"
    return False, f"安装失败：{out.strip()[:300]}"
