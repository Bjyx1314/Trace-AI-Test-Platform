"""飞书项目(Meego) plugin 客户端：批量拉取生产缺陷工作项。
凭据来自环境变量(settings)，空间/字段来自项目配置。本期只做同步读取，不回写。
"""
from __future__ import annotations
import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)
_BASE = "https://project.feishu.cn/open_api"


def is_configured() -> bool:
    return bool(getattr(settings, "feishu_project_plugin_id", None)
               and getattr(settings, "feishu_project_plugin_secret", None))


def parse_defect(raw: dict, rootcause_field: str) -> dict:
    fields = {f.get("field_key"): f.get("field_value") for f in (raw.get("fields") or [])}
    return {
        "external_id": raw.get("id") or raw.get("work_item_id"),
        "title": raw.get("name") or "",
        "root_cause": (fields.get(rootcause_field) or "") if rootcause_field else "",
        "url": raw.get("url") or "",
    }


async def _plugin_token() -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_BASE}/authen/plugin_token", json={
            "plugin_id": settings.feishu_project_plugin_id,
            "plugin_secret": settings.feishu_project_plugin_secret,
            "type": 0,
        })
        r.raise_for_status()
        return r.json()["data"]["token"]


async def list_production_defects(space_id: str, defect_filter: dict) -> list[dict]:
    """按筛选条件列出生产缺陷工作项原始记录。凭据/空间缺失则返回 []。"""
    if not is_configured() or not space_id:
        return []
    token = await _plugin_token()
    headers = {"X-PLUGIN-TOKEN": token, "X-USER-KEY": getattr(settings, "feishu_project_user_key", "")}
    payload = {"work_item_type_key": (defect_filter or {}).get("work_item_type_key", "bug"),
               **(defect_filter or {}).get("query", {})}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_BASE}/{space_id}/work_item/filter", json=payload, headers=headers)
        r.raise_for_status()
        return r.json().get("data", []) or []


async def get_work_item(space_id: str, work_item_id: str) -> dict:
    if not is_configured():
        return {}
    token = await _plugin_token()
    headers = {"X-PLUGIN-TOKEN": token, "X-USER-KEY": getattr(settings, "feishu_project_user_key", "")}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_BASE}/{space_id}/work_item/{work_item_id}", headers=headers)
        r.raise_for_status()
        return (r.json().get("data") or {})
