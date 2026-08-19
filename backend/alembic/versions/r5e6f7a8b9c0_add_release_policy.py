"""add release_policy to quality_gate_configs (AI质量闭环阶段六:发布决策门禁)

Revision ID: r5e6f7a8b9c0
Revises: r4d5e6f7a8b9
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r5e6f7a8b9c0'
down_revision: Union[str, None] = 'r4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('quality_gate_configs', sa.Column('release_policy', sa.String(length=20), nullable=False, server_default='advisory'))


def downgrade() -> None:
    op.drop_column('quality_gate_configs', 'release_policy')
