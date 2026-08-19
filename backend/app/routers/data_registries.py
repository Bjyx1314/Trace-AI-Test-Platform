"""数据编排注册表管理（方案 §23，MVP-1 基座）：数据对象 Schema / 数据能力 / 数据场景。

本增量只做注册 + 生命周期流转（能力 DRAFT→ACTIVE、场景 DRAFT→ACTIVE）与查询。
能力的"试运行认证"、场景发布的 postconditions 覆盖校验，随执行引擎（Resolver/Orchestration）到位后接。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import DataObjectSchema, DataCapability, DataScenario

router = APIRouter(prefix="/api", tags=["data-registries"])


def _dump(row, cols) -> dict:
    return {c: getattr(row, c) for c in cols}


_SCHEMA_COLS = ("id", "data_type", "schema_version", "schema_json", "status", "owner", "description")
_CAP_COLS = ("id", "capability_id", "version", "name", "provider_type", "business_domain", "executor_ref",
             "input_schema", "parameter_mapping", "output_extract", "idempotency_supported", "max_concurrency",
             "rate_limit_per_minute", "timeout_seconds", "sla_seconds", "side_effects", "cleanup_mode",
             "cleanup_capability_id", "supports_strong_rollback", "retention_hours", "supported_environments",
             "owner", "status", "approval_status", "last_verify")
_SCN_COLS = ("id", "scenario_id", "version", "name", "data_type", "provides", "supported_schema_versions",
             "supported_environments", "supported_constraints", "guarantees", "workflow", "postconditions",
             "outputs", "credentials", "status", "owner")

_CAP_WRITABLE = set(_CAP_COLS) - {"id", "status", "approval_status", "last_verify"}
_SCN_WRITABLE = set(_SCN_COLS) - {"id", "status"}
_SCHEMA_WRITABLE = set(_SCHEMA_COLS) - {"id"}


# ── 数据对象 Schema ────────────────────────────────────────────────────────────
@router.get("/data-object-schemas")
async def list_schemas(db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    rows = (await db.execute(select(DataObjectSchema).order_by(DataObjectSchema.data_type))).scalars().all()
    return [_dump(r, _SCHEMA_COLS) for r in rows]


@router.post("/data-object-schemas")
async def upsert_schema(body: dict, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    data = {k: v for k, v in (body or {}).items() if k in _SCHEMA_WRITABLE}
    dt, ver = str(data.get("data_type") or "").strip(), str(data.get("schema_version") or "").strip()
    if not dt or not ver:
        raise HTTPException(400, "data_type 与 schema_version 必填")
    row = (await db.execute(select(DataObjectSchema).where(
        DataObjectSchema.data_type == dt, DataObjectSchema.schema_version == ver))).scalars().first()
    if row:
        for k, v in data.items():
            setattr(row, k, v)
    else:
        row = DataObjectSchema(**data); db.add(row)
    await db.commit(); await db.refresh(row)
    return _dump(row, _SCHEMA_COLS)


# ── 数据能力 ──────────────────────────────────────────────────────────────────
@router.get("/data-capabilities")
async def list_capabilities(db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    rows = (await db.execute(select(DataCapability).order_by(DataCapability.capability_id))).scalars().all()
    return [_dump(r, _CAP_COLS) for r in rows]


@router.post("/data-capabilities")
async def upsert_capability(body: dict, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    """注册/更新能力（DRAFT）。按 capability_id+version 幂等。"""
    data = {k: v for k, v in (body or {}).items() if k in _CAP_WRITABLE}
    cid, ver = str(data.get("capability_id") or "").strip(), str(data.get("version") or "").strip()
    if not cid or not ver or not (data.get("provider_type") or "").strip():
        raise HTTPException(400, "capability_id、version、provider_type 必填")
    row = (await db.execute(select(DataCapability).where(
        DataCapability.capability_id == cid, DataCapability.version == ver))).scalars().first()
    if row:
        for k, v in data.items():
            setattr(row, k, v)
    else:
        row = DataCapability(**data); db.add(row)
    await db.commit(); await db.refresh(row)
    return _dump(row, _CAP_COLS)


@router.post("/data-capabilities/{cap_id}/activate")
async def activate_capability(cap_id: str, env: str = "sit", force: bool = False,
                              db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    """激活能力：先【真跑一次】试运行认证，通过才置 ACTIVE+APPROVED。

    原来这里只是改个状态、从不验证，于是"已认证"的能力可能根本调不通，等到造数时才炸。
    现在重放一次、按业务码判定，结果写进 last_verify。
    force=true 跳过认证直接激活（破坏性能力人工确认后走这条）。
    """
    row = await db.get(DataCapability, cap_id)
    if not row:
        raise HTTPException(404, "能力不存在")
    if force:
        row.status = "ACTIVE"; row.approval_status = "APPROVED"
        row.last_verify = {"ok": True, "forced": True, "by": user.get("sub"),
                           "at": datetime.now().isoformat(timespec="seconds")}
        row.updated_at = datetime.now()
        await db.commit()
        return _dump(row, _CAP_COLS)

    from app.services.data_prep.sediment import verify_capability
    rec = await verify_capability(db, row, env)
    if not rec.get("ok"):
        raise HTTPException(400, f"试运行认证未通过，未激活：{rec.get('error') or rec.get('skipped') or '未知原因'}")
    return _dump(row, _CAP_COLS)


@router.post("/data-capabilities/{cap_id}/disable")
async def disable_capability(cap_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    row = await db.get(DataCapability, cap_id)
    if not row:
        raise HTTPException(404, "能力不存在")
    row.status = "DISABLED"; row.updated_at = datetime.now()
    await db.commit()
    return _dump(row, _CAP_COLS)


@router.delete("/data-capabilities/{cap_id}")
async def delete_capability(cap_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    row = await db.get(DataCapability, cap_id)
    if not row:
        raise HTTPException(404, "能力不存在")
    await db.delete(row); await db.commit()
    return {"ok": True}


# ── 数据场景 ──────────────────────────────────────────────────────────────────
@router.get("/data-scenarios")
async def list_scenarios(db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    rows = (await db.execute(select(DataScenario).order_by(DataScenario.scenario_id))).scalars().all()
    return [_dump(r, _SCN_COLS) for r in rows]


@router.post("/data-scenarios")
async def upsert_scenario(body: dict, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    data = {k: v for k, v in (body or {}).items() if k in _SCN_WRITABLE}
    sid, ver = str(data.get("scenario_id") or "").strip(), str(data.get("version") or "").strip()
    if not sid or not ver:
        raise HTTPException(400, "scenario_id、version 必填")
    row = (await db.execute(select(DataScenario).where(
        DataScenario.scenario_id == sid, DataScenario.version == ver))).scalars().first()
    if row:
        for k, v in data.items():
            setattr(row, k, v)
    else:
        row = DataScenario(**data); db.add(row)
    await db.commit(); await db.refresh(row)
    return _dump(row, _SCN_COLS)


@router.post("/data-scenarios/{scn_id}/publish")
async def publish_scenario(scn_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    """发布场景（ACTIVE）。postconditions 覆盖 guarantees 的门禁校验随执行引擎到位后接。"""
    row = await db.get(DataScenario, scn_id)
    if not row:
        raise HTTPException(404, "场景不存在")
    row.status = "ACTIVE"; row.updated_at = datetime.now()
    await db.commit()
    return _dump(row, _SCN_COLS)


@router.delete("/data-scenarios/{scn_id}")
async def delete_scenario(scn_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user)):
    row = await db.get(DataScenario, scn_id)
    if not row:
        raise HTTPException(404, "场景不存在")
    await db.delete(row); await db.commit()
    return {"ok": True}
