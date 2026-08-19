"""add change_impact_records (代码影响分析记录)

方案 8.4/14.2：skill platform mode 输出 + 状态。手动触发(paste_diff/local_path/repo_branch)。
幂等键 (business_repo_id, mr_id, head_sha)。

Revision ID: q4d5e6f7a8b9
Revises: q3c4d5e6f7a8
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'q4d5e6f7a8b9'
down_revision: Union[str, None] = 'q3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'change_impact_records',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('requirement_id', sa.String(length=36), nullable=True),
        sa.Column('business_repo_id', sa.String(length=36), nullable=True),
        sa.Column('trigger_mode', sa.String(length=20), nullable=False, server_default='paste_diff'),
        sa.Column('mr_id', sa.String(length=100), nullable=True),
        sa.Column('repo_label', sa.String(length=200), nullable=True),
        sa.Column('base_branch', sa.String(length=100), nullable=True),
        sa.Column('target_branch', sa.String(length=100), nullable=True),
        sa.Column('head_sha', sa.String(length=60), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('schema_version', sa.String(length=20), nullable=True),
        sa.Column('impact_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('impact_md', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ),
        sa.ForeignKeyConstraint(['business_repo_id'], ['business_repos.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_repo_id', 'mr_id', 'head_sha', name='uq_change_impact_idem'),
    )
    op.create_index('ix_change_impact_records_requirement_id', 'change_impact_records', ['requirement_id'])


def downgrade() -> None:
    op.drop_index('ix_change_impact_records_requirement_id', table_name='change_impact_records')
    op.drop_table('change_impact_records')
