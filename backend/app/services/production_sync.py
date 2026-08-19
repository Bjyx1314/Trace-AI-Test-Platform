"""批量同步生产缺陷 → 沉淀经验。手动触发；抽成可复用函数，供后续定时/webhook。"""
from __future__ import annotations
import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Defect, Project
from app.services import feishu_project
from app.services.experience_miner import sediment_from_bug

logger = logging.getLogger(__name__)


def filter_new_defects(parsed: list[dict], existing_external_ids: set[str]) -> list[dict]:
    """去重：过滤掉已同步(external_id 已存在)与无 external_id 的记录。"""
    out = []
    for d in parsed or []:
        eid = (d.get("external_id") or "").strip()
        if eid and eid not in existing_external_ids:
            out.append(d)
    return out


async def sync_production_defects(db: AsyncSession, project_id: str) -> dict:
    proj = await db.get(Project, project_id)
    if not proj or not proj.feishu_project_space_id or not feishu_project.is_configured():
        return {"synced": 0, "sedimented": 0, "skipped": 0, "reason": "未配置飞书项目或凭据"}

    raw = await feishu_project.list_production_defects(
        proj.feishu_project_space_id, proj.feishu_project_defect_filter or {})
    parsed = [feishu_project.parse_defect(r, proj.feishu_project_rootcause_field or "") for r in raw]

    existing = set((await db.execute(
        select(Defect.external_ticket_id).where(Defect.external_source == "feishu_project")
    )).scalars().all())
    fresh = filter_new_defects(parsed, existing)

    sedimented = 0
    for d in fresh:
        defect = Defect(
            id=str(uuid.uuid4()), title=d["title"][:500], source="production",
            external_source="feishu_project", external_ticket_id=d["external_id"],
            external_ticket_url=d.get("url"), root_cause=d.get("root_cause") or None,
            status="confirmed",
        )
        db.add(defect)
        await db.flush()
        try:
            if await sediment_from_bug(db, defect.id):
                sedimented += 1
        except Exception:  # noqa: BLE001
            pass
    await db.commit()
    return {"synced": len(fresh), "sedimented": sedimented, "skipped": len(parsed) - len(fresh)}
