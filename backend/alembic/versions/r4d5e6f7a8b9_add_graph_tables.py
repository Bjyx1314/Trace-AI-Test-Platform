"""add graph_node/graph_edge + page cache graph_node_id (AI质量闭环阶段五:代码事实图谱)

方案 9.5 DDL。PG 关系表存图，2 跳扩散用递归 CTE 毫秒级完成，不引入图数据库。

Revision ID: r4d5e6f7a8b9
Revises: r3c4d5e6f7a8
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'r4d5e6f7a8b9'
down_revision: Union[str, None] = 'r3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('page_structure_caches', sa.Column('graph_node_id', sa.String(length=300), nullable=True))
    op.create_index('ix_page_structure_caches_graph_node_id', 'page_structure_caches', ['graph_node_id'])

    op.create_table(
        'graph_node',
        sa.Column('node_id', sa.String(length=400), nullable=False),
        sa.Column('node_type', sa.String(length=20), nullable=False),
        sa.Column('repo', sa.String(length=200), nullable=True),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('attrs', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('seen_in_version', sa.String(length=60), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('node_id'),
    )
    op.create_index('ix_graph_node_node_type', 'graph_node', ['node_type'])
    op.create_index('ix_graph_node_repo', 'graph_node', ['repo'])
    op.create_index('ix_graph_node_status', 'graph_node', ['status'])

    op.create_table(
        'graph_edge',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('from_node', sa.String(length=400), nullable=False),
        sa.Column('to_node', sa.String(length=400), nullable=False),
        sa.Column('edge_type', sa.String(length=30), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('seen_in_version', sa.String(length=60), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('last_verified_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('from_node', 'to_node', 'edge_type', 'source', name='uq_graph_edge'),
    )
    op.create_index('ix_graph_edge_from_node', 'graph_edge', ['from_node'])
    op.create_index('ix_graph_edge_to_node', 'graph_edge', ['to_node'])


def downgrade() -> None:
    op.drop_table('graph_edge')
    op.drop_table('graph_node')
    op.drop_index('ix_page_structure_caches_graph_node_id', table_name='page_structure_caches')
    op.drop_column('page_structure_caches', 'graph_node_id')
