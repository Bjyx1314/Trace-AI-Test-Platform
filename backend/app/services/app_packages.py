"""App 测试包版本查询 / 包版本→下载信息解析（执行前换包用）。

数据源：Jenkins 构建记录接口 settings.jenkins_build_api
  POST {jenkins_build_api}  body={"projectName": f"{jenkins_project_prefix}{产品名}"}
  → {"success":true,"data":[{id,projectName,buildNumber,downloadUrl,packageParams,buildTime,...}]}
只取安卓包（packageParams 含 Platform=android）。未配置接口/查询失败 → 返回空（下拉无可选版本）。

返回结构（保持不变，供 executions 路由与 execution_runner 复用）：
- list_packages(app) -> [{"id","label","version"}]      # 下拉选项，label="buildNumber  buildTime"
- resolve_package(app, package_id) -> {"source","package"} | None
    source: 交给 apk.install_apk 的来源（此处为 downloadUrl，http(s) apk 直链）
    package: 旧包 android 包名；此处恒为 None，由「枚举管理 → 端 → 应用包名」枚举兜底
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _build_id(b: dict) -> str:
    """构建记录的稳定标识：优先 id，兜底 buildNumber。"""
    v = b.get("id")
    return str(v) if v is not None else str(b.get("buildNumber") or "")


def _is_android(b: dict) -> bool:
    return "android" in (b.get("packageParams") or "").lower()


def _build_version(b: dict) -> str:
    """从 packageParams（k=v;k=v）里取 BuildVersion，取不到退化为 buildNumber。"""
    for kv in (b.get("packageParams") or "").split(";"):
        k, _, v = kv.partition("=")
        if k.strip().lower() == "buildversion":
            return v.strip()
    return str(b.get("buildNumber") or "")


def _fetch_builds(app: str) -> list[dict]:
    """查 Jenkins 该产品（app 端名）的安卓构建列表。未配置/失败返回 []（不抛，不阻断换包流程）。"""
    api = (settings.jenkins_build_api or "").strip()
    if not api or not app:
        return []
    project = f"{settings.jenkins_project_prefix}{app}"   # 查询时产品名前加「」
    try:
        import httpx
        r = httpx.post(api, json={"projectName": project}, timeout=12)
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
        return [b for b in rows if isinstance(b, dict) and _is_android(b)]
    except Exception as e:
        logger.warning("查询 Jenkins 测试包失败(app=%s, project=%s)：%s", app, project, e)
        return []


def list_packages(app: str) -> list[dict]:
    """某 app 端可选的测试包版本（下拉数据源）。label = "buildNumber  buildTime"（只展示字段值）。"""
    out: list[dict] = []
    for b in _fetch_builds(app):
        bid = _build_id(b)
        if not bid:
            continue
        out.append({
            "id": bid,
            "label": f"{b.get('buildNumber', '')}  {b.get('buildTime', '')}".strip(),
            "version": _build_version(b),
        })
    return out


def resolve_package(app: str, package_id: str) -> dict | None:
    """把所选构建解析成 {source, package}：source=该构建 downloadUrl（apk 直链），package=None（包名由枚举兜底）。"""
    for b in _fetch_builds(app):
        if _build_id(b) == str(package_id):
            src = (b.get("downloadUrl") or "").strip()
            return {"source": src, "package": None} if src else None
    return None
