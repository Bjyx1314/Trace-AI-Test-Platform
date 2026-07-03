"""add requirement source_record_id

Revision ID: aec91f6ed4f2
Revises: 1fe20ab2d8d2
Create Date: 2026-06-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'aec91f6ed4f2'
down_revision: Union[str, None] = '1fe20ab2d8d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('requirements', sa.Column('source_record_id', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('requirements', 'source_record_id')
