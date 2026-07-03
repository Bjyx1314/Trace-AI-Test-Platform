from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models import EnumDefinition, EnumLog
from app.schemas import EnumCreate, EnumOut
from app.dependencies import require_admin

router = APIRouter(prefix="/api/enums", tags=["enums"])


def _operator_name(cu: dict | None) -> str:
    """枚举操作记录里的操作人：优先真实姓名(name)，退而邮箱/账号，兜底"系统"。"""
    cu = cu or {}
    return cu.get("name") or cu.get("email") or cu.get("sub") or "系统"


@router.get("", response_model=list[EnumOut])
async def list_enums(category: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(EnumDefinition).order_by(EnumDefinition.category, EnumDefinition.sort_order)
    if category:
        q = q.where(EnumDefinition.category == category)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/url-matrix")
async def url_matrix(db: AsyncSession = Depends(get_db)):
    """PC 端地址矩阵：行=端(platform 组 parent_key=pc)，列=环境(sit/dev…)，单元格=已配地址(含 enum id)。

    地址按环境存于不同分类(env_category)：sit→base_url，dev→base_url_dev…；直接取 label 即 URL，
    不做任何 label 编码解析。未配置的单元格返回 None，供前端展示"未配置 + 配置"入口。"""
    from app.services.environments import ENVIRONMENTS, env_category

    # 端列表：platform 组 pc 分组，按 sort_order
    pc_platforms = (await db.execute(
        select(EnumDefinition)
        .where(EnumDefinition.category == "platform", EnumDefinition.parent_key == "pc")
        .order_by(EnumDefinition.sort_order)
    )).scalars().all()
    plat_label = {p.key: p.label for p in pc_platforms}
    order = [p.key for p in pc_platforms]

    # 各环境已配地址：{env_key: {端名: {id, url}}}
    by_env: dict[str, dict[str, dict]] = {}
    for e in ENVIRONMENTS:
        rows = (await db.execute(
            select(EnumDefinition).where(EnumDefinition.category == env_category(e["key"]))
        )).scalars().all()
        by_env[e["key"]] = {r.key: {"id": r.id, "url": (r.label or "").strip()} for r in rows if (r.label or "").strip()}

    # 端行 = pc 端 ∪ 任意环境已配但不在 pc 列表里的端(不丢历史数据)，pc 端在前保序
    extra = sorted({k for m in by_env.values() for k in m} - set(order))
    all_keys = order + extra

    # envs 带上写入分类，前端新增单元格时用它 POST /api/enums(category, key=端名, label=URL)
    envs = [{"key": e["key"], "label": e["label"], "category": env_category(e["key"])} for e in ENVIRONMENTS]

    return {
        "envs": envs,
        "platforms": [
            {
                "key": k,
                "label": plat_label.get(k, k),
                "urls": {e["key"]: by_env[e["key"]].get(k) for e in ENVIRONMENTS},
            }
            for k in all_keys
        ],
    }


@router.get("/logs")
async def list_enum_logs(category: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(EnumLog)
        .where(EnumLog.category == category)
        .order_by(EnumLog.created_at.desc())
    )).scalars().all()
    return [
        {
            "id": r.id,
            "operation": r.operation,
            "value": r.value,
            "operator": r.operator,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("", response_model=EnumOut, status_code=201)
async def create_enum(body: EnumCreate, db: AsyncSession = Depends(get_db), current_admin: dict = Depends(require_admin)):
    en = EnumDefinition(**body.model_dump(exclude={"is_active"}))  # is_active 用列默认(True)，不由创建体决定
    db.add(en)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, f"枚举 ({body.category}, {body.key}) 已存在")
    await db.refresh(en)
    db.add(EnumLog(category=en.category, enum_id=en.id, operation="create", value=en.label, operator=_operator_name(current_admin)))
    await db.commit()
    return en


@router.put("/{enum_id}", response_model=EnumOut)
async def update_enum(enum_id: str, body: EnumCreate, db: AsyncSession = Depends(get_db), current_admin: dict = Depends(require_admin)):
    en = await db.get(EnumDefinition, enum_id)
    if not en:
        raise HTTPException(404, "Enum not found")
    old_label = en.label
    # is_active 单独处理：仅在明确传入(非 None)时改；其它字段照旧整体覆盖
    for k, v in body.model_dump(exclude={"is_active"}).items():
        setattr(en, k, v)
    if body.is_active is not None:
        en.is_active = body.is_active
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, f"枚举 ({body.category}, {body.key}) 已存在")
    await db.refresh(en)
    value_note = f"{old_label} → {en.label}" if old_label != en.label else en.label
    db.add(EnumLog(category=en.category, enum_id=en.id, operation="update", value=value_note, operator=_operator_name(current_admin)))
    await db.commit()
    return en


@router.delete("/{enum_id}", status_code=204)
async def delete_enum(enum_id: str, db: AsyncSession = Depends(get_db), current_admin: dict = Depends(require_admin)):
    en = await db.get(EnumDefinition, enum_id)
    if not en:
        raise HTTPException(404, "Enum not found")
    db.add(EnumLog(category=en.category, enum_id=en.id, operation="delete", value=en.label, operator=_operator_name(current_admin)))
    await db.delete(en)
    await db.commit()
