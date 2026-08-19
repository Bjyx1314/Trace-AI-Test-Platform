"""add defect root_cause/external_source + feishu project config, relax defect FKs nullable

生产缺陷经验(P2)：Defect 加 root_cause/external_source；三个内部关联外键放开为可空
(生产缺陷无内部执行/用例关联)；Project 加飞书项目(Meego)同步配置字段。

Revision ID: r6f7a8b9c0d1
Revises: r5e6f7a8b9c0
Create Date: 2026-07-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'r6f7a8b9c0d1'
down_revision: Union[str, None] = 'r5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Defect: 生产缺陷根因与外部来源
    op.add_column('defects', sa.Column('root_cause', sa.Text(), nullable=True))
    op.add_column('defects', sa.Column('external_source', sa.String(length=30), nullable=True))
    # Defect: 放开内部关联外键为可空(生产缺陷无内部执行/用例关联)
    op.alter_column('defects', 'test_result_id', existing_type=sa.String(length=36), nullable=True)
    op.alter_column('defects', 'execution_id', existing_type=sa.String(length=36), nullable=True)
    op.alter_column('defects', 'test_case_id', existing_type=sa.String(length=36), nullable=True)
    # Project: 飞书项目(Meego)生产缺陷同步配置
    op.add_column('projects', sa.Column('feishu_project_space_id', sa.String(length=100), nullable=True))
    op.add_column('projects', sa.Column('feishu_project_defect_filter', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('projects', sa.Column('feishu_project_rootcause_field', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'feishu_project_rootcause_field')
    op.drop_column('projects', 'feishu_project_defect_filter')
    op.drop_column('projects', 'feishu_project_space_id')
    op.alter_column('defects', 'test_case_id', existing_type=sa.String(length=36), nullable=False)
    op.alter_column('defects', 'execution_id', existing_type=sa.String(length=36), nullable=False)
    op.alter_column('defects', 'test_result_id', existing_type=sa.String(length=36), nullable=False)
    op.drop_column('defects', 'external_source')
    op.drop_column('defects', 'root_cause')
