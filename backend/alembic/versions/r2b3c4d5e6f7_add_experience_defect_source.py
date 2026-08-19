"""add experiences table + defect source/retrospect (AI质量闭环阶段三:经验库+逃逸回溯)

Revision ID: r2b3c4d5e6f7
Revises: r1a2b3c4d5e6
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

from app.config import settings


revision: str = 'r2b3c4d5e6f7'
down_revision: Union[str, None] = 'r1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIM = settings.embed_dim


def upgrade() -> None:
    # Defect 加列：逃逸回溯
    op.add_column('defects', sa.Column('source', sa.String(length=20), nullable=False, server_default='execution'))
    op.add_column('defects', sa.Column('covered_item_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('defects', sa.Column('retrospect', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # ReviewFeedback 加列：已沉淀经验关联
    op.add_column('review_feedbacks', sa.Column('experience_id', sa.String(length=36), nullable=True))
    op.create_index('ix_review_feedbacks_experience_id', 'review_feedbacks', ['experience_id'])

    # Experience 经验库
    op.create_table(
        'experiences',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('trigger_context', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('suggested_covered_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('source', sa.String(length=30), nullable=False, server_default='tester_feedback'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('embedding', Vector(_DIM), nullable=True),
        sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='{"hit_count": 0, "adopt_count": 0, "reject_count": 0}'),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='candidate'),
        sa.Column('merged_from', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_experiences_project_id', 'experiences', ['project_id'])
    op.create_index('ix_experiences_status', 'experiences', ['status'])
    # ivfflat 向量索引（cosine）；lists 按数据量调，起步 100
    op.execute("CREATE INDEX IF NOT EXISTS ix_experiences_embedding "
               "ON experiences USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_experiences_embedding")
    op.drop_index('ix_experiences_status', table_name='experiences')
    op.drop_index('ix_experiences_project_id', table_name='experiences')
    op.drop_table('experiences')
    op.drop_index('ix_review_feedbacks_experience_id', table_name='review_feedbacks')
    op.drop_column('review_feedbacks', 'experience_id')
    op.drop_column('defects', 'retrospect')
    op.drop_column('defects', 'covered_item_ids')
    op.drop_column('defects', 'source')
