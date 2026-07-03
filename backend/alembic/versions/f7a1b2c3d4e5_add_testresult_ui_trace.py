"""add ui_trace to test_results (App真机执行每步截图轨迹)

Revision ID: f7a1b2c3d4e5
Revises: e6e70c44cc59
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f7a1b2c3d4e5'
down_revision: Union[str, None] = 'e6e70c44cc59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_results', sa.Column('ui_trace', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('test_results', 'ui_trace')
