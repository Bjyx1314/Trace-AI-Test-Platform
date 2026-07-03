"""add requirement_slices + test_cases.slice_id (需求切片：多人多范围)

阶段1：新增 requirement_slices 表与 test_cases.slice_id。把每条现有需求迁移为一条
is_default 的「全文」切片(复制 analysis_result/confirmation/owner/status)，并回填用例 slice_id。
纯增量，不改现有读写行为。

Revision ID: m7a8b9c0d1e2
Revises: l6f7a8b9c0d1
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'm7a8b9c0d1e2'
down_revision: Union[str, None] = 'l6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'requirement_slices',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('requirement_id', sa.String(length=36), sa.ForeignKey('requirements.id'), nullable=False, index=True),
        sa.Column('owner_name', sa.String(length=200), nullable=True),
        sa.Column('scope_label', sa.String(length=200), nullable=False, server_default='全文'),
        sa.Column('scope_text', sa.Text(), nullable=True),
        sa.Column('scope_image_tokens', postgresql.JSONB(), nullable=True),
        sa.Column('analysis_result', postgresql.JSONB(), nullable=True),
        sa.Column('analysis_confirmation', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='pending_analysis'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.add_column('test_cases', sa.Column('slice_id', sa.String(length=36), nullable=True))
    op.create_foreign_key('fk_test_cases_slice', 'test_cases', 'requirement_slices', ['slice_id'], ['id'])
    op.create_index('ix_test_cases_slice_id', 'test_cases', ['slice_id'])

    # 现有需求 → 一条「全文」默认切片(复制分析/确认/归属/状态)
    op.execute(
        "INSERT INTO requirement_slices "
        "(id, requirement_id, owner_name, scope_label, analysis_result, analysis_confirmation, status, is_default, created_at, updated_at) "
        "SELECT gen_random_uuid()::text, r.id, r.owner_name, '全文', r.analysis_result, r.analysis_confirmation, r.status, true, now(), now() "
        "FROM requirements r"
    )
    # 回填用例 slice_id → 其需求的默认切片
    op.execute(
        "UPDATE test_cases tc SET slice_id = s.id "
        "FROM requirement_slices s "
        "WHERE s.requirement_id = tc.requirement_id AND s.is_default = true AND tc.requirement_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index('ix_test_cases_slice_id', table_name='test_cases')
    op.drop_constraint('fk_test_cases_slice', 'test_cases', type_='foreignkey')
    op.drop_column('test_cases', 'slice_id')
    op.drop_table('requirement_slices')
