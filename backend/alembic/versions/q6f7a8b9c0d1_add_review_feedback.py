"""add review_feedbacks (测试Review反馈留痕)

方案 11.3：记录对覆盖项的增删改，为阶段三经验沉淀铺垫。

Revision ID: q6f7a8b9c0d1
Revises: q5e6f7a8b9c0
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'q6f7a8b9c0d1'
down_revision: Union[str, None] = 'q5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'review_feedbacks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('test_case_id', sa.String(length=36), nullable=False),
        sa.Column('requirement_id', sa.String(length=36), nullable=True),
        sa.Column('target_type', sa.String(length=30), nullable=False, server_default='covered_item'),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('operator', sa.String(length=100), nullable=False, server_default='系统'),
        sa.Column('found_bug_later', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_feedbacks_test_case_id', 'review_feedbacks', ['test_case_id'])
    op.create_index('ix_review_feedbacks_requirement_id', 'review_feedbacks', ['requirement_id'])


def downgrade() -> None:
    op.drop_index('ix_review_feedbacks_requirement_id', table_name='review_feedbacks')
    op.drop_index('ix_review_feedbacks_test_case_id', table_name='review_feedbacks')
    op.drop_table('review_feedbacks')
