"""PC web 执行登录态保活 —— 可选复用外部 PC 自动化框架的登录机制。

设计原则：登录流程、TTL/失效判定、重登都由框架负责，本模块只做「编排 + 消费」，零重写。
- 判定是否需要重登：复用框架 common.utils.auth_state.is_state_usable(TTL) + 一次轻量运行时探测
  (打开被测地址看是否被重定向回 /login，覆盖「服务端提前踢登录」的情况)。
- 需要重登(新端 / TTL 过期 / 被踢)：以子进程跑框架自带的「登录冒烟测试」，触发其 ensure_storage_state
  fixture 自动登录并把 storageState 存到框架 state 目录；本模块返回该 state 文件路径供执行注入。
- 账号是框架 projects.yaml 维护的(唯一真源)，账号变更只改那里，这里无需改动。

未配置外部框架映射的端回退到平台本地手动登录态 login_states/<端>.json，
该文件可由 tools/capture_login.py 手动抓取。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

from app.config import settings

# 外部 PC 自动化框架 checkout 路径。
FRAMEWORK_ROOT = Path(os.environ.get("FRAMEWORK_ROOT") or "./frameworks/web")
_PROJECTS_YAML = FRAMEWORK_ROOT / "common" / "config" / "projects.yaml"

# 通过 JSON 环境变量配置端与外部框架的映射，仓库不预置任何业务系统。
# 示例：{"web-admin":{"project":"demo","web":"main","auth_type":"password",
# "tenant":false,"flow_class":"my_flows.login:LoginFlow","smoke":"tests/test_login.py"}}
def _load_platform_mapping() -> dict[str, dict]:
    raw = os.environ.get("FRAMEWORK_PLATFORM_MAP_JSON") or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


_PLATFORM_TO_FRAMEWORK = _load_platform_mapping()
_UNIVERSAL_SMOKE = os.environ.get("FRAMEWORK_LOGIN_SMOKE") or ""

_SMOKE_TIMEOUT = 240  # 单端登录冒烟最长等待(秒)
_TEMP_LOGIN_TIMEOUT = 180  # 临时账号登录最长等待(秒)


def framework_covers(platform: str) -> bool:
    return platform in _PLATFORM_TO_FRAMEWORK and _framework_available()


def launch_args_for(platform: str) -> list[str]:
    """该端在框架 projects.yaml 配的 browser_args(如 finance 的 --no-proxy-server)，
    供执行时浏览器启动对齐，避免代理等差异导致页面打不开。"""
    fw = _PLATFORM_TO_FRAMEWORK.get(platform)
    if not fw or not _framework_available():
        return []
    try:
        cfg = yaml.safe_load(_PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    proj = (cfg.get("projects") or {}).get(fw["project"]) or {}
    return [str(a) for a in (proj.get("browser_args") or []) if str(a).strip()]


def platform_for_base_url(base_url: str) -> Optional[str]:
    """按被测地址反查端名(用于页面探索复用登录态)。匹配框架 projects.yaml 的 base_urls。"""
    target = (base_url or "").rstrip("/").lower()
    if not target or not _framework_available():
        return None
    for plat, fw in _PLATFORM_TO_FRAMEWORK.items():
        try:
            _, _, burl, _ = _framework_meta(fw["project"], fw["web"], "default")
        except Exception:
            continue
        if burl and burl.rstrip("/").lower() == target:
            return plat
    return None


def account_meta(platform: str) -> dict:
    """某端的账号/登录元信息，供执行弹框渲染：
    {covered, auth_type(password|sms_code), tenant(bool), accounts:[{role,label}]}。
    """
    fw = _PLATFORM_TO_FRAMEWORK.get(platform)
    if not fw or not _framework_available():
        return {"covered": False, "auth_type": None, "tenant": False, "accounts": []}
    return {
        "covered": True,
        "auth_type": fw.get("auth_type", "password"),
        "tenant": bool(fw.get("tenant")),
        "accounts": list_accounts(platform),
    }


def list_accounts(platform: str) -> list[dict]:
    """列出某端在框架 projects.yaml 里已配的账号(只读)。返回 [{role, label}]。

    label 优先 租户名，其次脱敏用户名；供执行弹框下拉展示。不返回密码。
    """
    fw = _PLATFORM_TO_FRAMEWORK.get(platform)
    if not fw or not _framework_available():
        return []
    try:
        cfg = yaml.safe_load(_PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    proj = (cfg.get("projects") or {}).get(fw["project"]) or {}
    accounts = ((proj.get("auth") or {}).get("accounts") or {}).get(fw["web"]) or {}
    out: list[dict] = []
    if isinstance(accounts, dict):
        for role, acc in accounts.items():
            acc = acc or {}
            tenant = str(acc.get("tenant_name") or "").strip()
            user = str(acc.get("username") or "").strip()
            user_mask = (user[:3] + "***" + user[-2:]) if len(user) > 5 else (user or role)
            label = tenant or user_mask or role
            out.append({"role": role, "label": label})
    return out


def _framework_available() -> bool:
    return _PROJECTS_YAML.exists()


def _ensure_framework_on_path() -> None:
    root = str(FRAMEWORK_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


_auth_state_mod = None


def _load_auth_state():
    """直接按文件加载框架的 common/utils/auth_state.py，绕过 common/__init__.py。

    框架 common/__init__.py 顶部 `from appium import webdriver`——平台 python 未装 appium
    (appium 只在 fw-venv 里)，走包导入会 ModuleNotFoundError。auth_state.py 仅依赖标准库且
    自包含，故用 importlib 按源码文件单独加载，登录态的 TTL 判定/路径解析无需 appium。
    """
    global _auth_state_mod
    if _auth_state_mod is not None:
        return _auth_state_mod
    import importlib.util
    p = FRAMEWORK_ROOT / "common" / "utils" / "auth_state.py"
    spec = importlib.util.spec_from_file_location("_fw_auth_state", p)
    mod = importlib.util.module_from_spec(spec)
    # 必须在 exec 前登记到 sys.modules：auth_state.py 用 @dataclass，
    # dataclasses 内部会 sys.modules.get(cls.__module__) 反查本模块，否则 AttributeError。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _auth_state_mod = mod
    return mod


def _framework_meta(project: str, web: str, role: str) -> tuple[Optional[Path], Optional[Path], str, int]:
    """返回 (state_path, meta_path, base_url, ttl_seconds)。读 projects.yaml，不含任何登录逻辑。"""
    cfg = yaml.safe_load(_PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    proj = (cfg.get("projects") or {}).get(project) or {}
    auth = proj.get("auth") or {}
    state_dir = FRAMEWORK_ROOT / str(auth.get("state_dir") or "ui_web/state")
    ttl = int(auth.get("state_ttl_seconds") or 28800)
    base_url = str((proj.get("base_urls") or {}).get(web) or "").strip()

    state_path, meta_path = _load_auth_state().state_paths(state_dir, project, web, role)
    return state_path, meta_path, base_url, ttl


def _framework_default_secret(project: str, web: str) -> Optional[str]:
    """读框架 projects.yaml 里该端默认账号的 password/验证码(SIT 固定码)。临时手机号复用此码登录。"""
    try:
        cfg = yaml.safe_load(_PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    proj = (cfg.get("projects") or {}).get(project) or {}
    accounts = ((proj.get("auth") or {}).get("accounts") or {}).get(web) or {}
    if isinstance(accounts, dict):
        # 优先 default 角色，否则取第一个有 password 的
        cand = accounts.get("default") if isinstance(accounts.get("default"), dict) else None
        if not (cand and cand.get("password")):
            cand = next((a for a in accounts.values() if isinstance(a, dict) and a.get("password")), None)
        if cand and cand.get("password"):
            return str(cand["password"]).strip()
    return None


async def _state_still_logged_in(base_url: str, state_path: Path) -> bool:
    """轻量探测：用现有登录态打开被测地址，未被重定向到 /login 即视为仍登录。"""
    if not base_url or not state_path.exists():
        return False
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return True  # 没有 playwright 时不阻断，交由执行阶段处理
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            ctx = await b.new_context(storage_state=str(state_path))
            page = await ctx.new_page()
            try:
                await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1.5)
                ok = "/login" not in page.url.lower()
            finally:
                await b.close()
            return ok
    except Exception:
        return False


async def _refresh_via_framework(project: str, web: str, role: str, smoke: str) -> bool:
    """子进程跑框架登录冒烟，触发其 ensure_storage_state 自动重登并存盘。返回是否成功。
    框架自身依赖(appium 等)与平台隔离：优先用 FRAMEWORK_PYTHON(独立 venv)的解释器跑。"""
    py = os.environ.get("FRAMEWORK_PYTHON") or sys.executable
    proc = await asyncio.create_subprocess_exec(
        py, "-m", "pytest", smoke,
        "--project", project, "--web", web, "--role", role, "-q",
        cwd=str(FRAMEWORK_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_SMOKE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    if proc.returncode != 0:
        import logging
        logging.getLogger(__name__).warning(
            "框架登录冒烟失败(rc=%s) %s/%s：%s", proc.returncode, project, web,
            (out or b"").decode("utf-8", "ignore")[-800:])
    return proc.returncode == 0


async def ensure_login_state(platform: str, role: str = "default") -> Optional[str]:
    """确保某端某账号有可用登录态，返回 storageState 文件路径(绝对)；无法提供则 None。

    流程(框架覆盖的端)：TTL 仍有效 且 探测仍登录 → 复用；否则跑框架冒烟重登(--role 指定账号)。
    框架未覆盖的端：回退平台本地手动登录态 login_states/<端>.json。
    """
    fw = _PLATFORM_TO_FRAMEWORK.get(platform)
    if fw and _framework_available():
        project, web = fw["project"], fw["web"]
        try:
            state_path, meta_path, base_url, ttl = _framework_meta(project, web, role)
        except Exception:
            state_path = None
        if state_path is not None:
            usable = _load_auth_state().is_state_usable(state_path, meta_path, ttl_seconds_fallback=ttl)
            if usable and await _state_still_logged_in(base_url, state_path):
                return str(state_path)
            smoke = fw.get("smoke") or _UNIVERSAL_SMOKE
            if smoke and await _refresh_via_framework(project, web, role, smoke):
                return str(state_path) if state_path.exists() else None
            # 重登失败：若旧态还在就先用旧态兜底，否则 None
            return str(state_path) if state_path.exists() else None

    # 框架未覆盖：回退平台本地手动登录态
    local = Path(settings.web_login_state_dir) / f"{platform}.json"
    return str(local) if local.exists() else None


async def login_temp(platform: str, username: str, password: str,
                     out_path: str, tenant_name: Optional[str] = None) -> bool:
    """用「临时账号」登录并把 storageState 存到 out_path(临时文件，用完即弃)。

    复用配置的外部框架登录流程，但【绝不写入框架配置/状态目录】，
    账号密码仅经环境变量传给子进程、不落盘、不入框架 yaml。返回是否成功。
    """
    fw = _PLATFORM_TO_FRAMEWORK.get(platform)
    if not fw or not _framework_available():
        return False
    try:
        _, _, base_url, _ = _framework_meta(fw["project"], fw["web"], "default")
    except Exception:
        base_url = ""
    if not base_url:
        return False

    # 验证码登录端：用户只给手机号时，可复用框架配置的测试验证码。
    if fw.get("auth_type") == "sms_code" and not password:
        password = _framework_default_secret(fw["project"], fw["web"]) or ""
    if not password:
        return False

    runner = Path(__file__).resolve().parents[2] / "tools" / "temp_login_runner.py"
    out_abs = str(Path(out_path).resolve())  # 子进程 cwd=框架根目录，必须用绝对路径
    import os
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "TL_FRAMEWORK_ROOT": str(FRAMEWORK_ROOT),
        "TL_FLOW_CLASS": fw.get("flow_class") or "",
        "TL_FLOW_TENANT": "true" if fw.get("tenant") else "false",
        "TL_BASE_URL": base_url,
        "TL_OUT": out_abs,
        "TL_USER": username,
        "TL_PASS": password,
        "TL_TENANT": tenant_name or "",
    }
    # 临时账号登录子进程同样跑框架登录流程(ui_web.flows.* → 经 common 依赖 appium)，
    # 必须用 FRAMEWORK_PYTHON(fw-venv，装了 appium/playwright)，平台 python 会缺 appium。
    py = os.environ.get("FRAMEWORK_PYTHON") or sys.executable
    proc = await asyncio.create_subprocess_exec(
        py, str(runner),
        cwd=str(FRAMEWORK_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=_TEMP_LOGIN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return proc.returncode == 0 and Path(out_abs).exists()
