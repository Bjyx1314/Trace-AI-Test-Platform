"""add covered_item_vecs (AI质量闭环阶段四: 覆盖项向量索引)

Revision ID: r3c4d5e6f7a8
Revises: r2b3c4d5e6f7
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from app.config import settings


revision: str = 'r3c4d5e6f7a8'
down_revision: Union[str, None] = 'r2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIM = settings.embed_dim


def upgrade() -> None:
    op.create_table(
        'covered_item_vecs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('item_id', sa.String(length=40), nullable=False),
        sa.Column('case_id', sa.String(length=36), nullable=False),
        sa.Column('requirement_id', sa.String(length=36), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('struct_key', sa.String(length=300), nullable=True),
        sa.Column('embedding', Vector(_DIM), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_id', 'item_id', name='uq_covered_item_vec'),
    )
    op.create_index('ix_covered_item_vecs_item_id', 'covered_item_vecs', ['item_id'])
    op.create_index('ix_covered_item_vecs_case_id', 'covered_item_vecs', ['case_id'])
    op.create_index('ix_covered_item_vecs_requirement_id', 'covered_item_vecs', ['requirement_id'])
    op.create_index('ix_covered_item_vecs_project_id', 'covered_item_vecs', ['project_id'])
    op.create_index('ix_covered_item_vecs_struct_key', 'covered_item_vecs', ['struct_key'])
    op.execute("CREATE INDEX IF NOT EXISTS ix_covered_item_vecs_embedding "
               "ON covered_item_vecs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_covered_item_vecs_embedding")
    op.drop_table('covered_item_vecs')
