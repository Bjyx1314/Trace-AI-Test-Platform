"""质量门禁配置 CRUD —— 按项目维度查看/修改 QualityGateConfig。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Project
from app.schemas import QualityGateConfigOut, QualityGateConfigUpdate
from app.services.quality_gate_engine import get_or_create_config

router = APIRouter(prefix="/api/projects", tags=["quality-gates"])


@router.get("/{project_id}/quality-gate-config", response_model=QualityGateConfigOut)
async def get_quality_gate_config(project_id: str, db: AsyncSession = Depends(get_db)):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    config = await get_or_create_config(db, project_id)
    await db.commit()
    await db.refresh(config)
    return config


@router.put("/{project_id}/quality-gate-config", response_model=QualityGateConfigOut)
async def update_quality_gate_config(
    project_id: str,
    body: QualityGateConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    config = await get_or_create_config(db, project_id)
    for k, v in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(config, k, v)

    await db.commit()
    await db.refresh(config)
    return config
