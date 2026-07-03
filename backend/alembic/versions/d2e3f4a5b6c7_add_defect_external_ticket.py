"""add external_ticket_id/url to defects (external tracker sync)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-23 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('defects', sa.Column('external_ticket_id', sa.String(length=100), nullable=True))
    op.add_column('defects', sa.Column('external_ticket_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('defects', 'external_ticket_url')
    op.drop_column('defects', 'external_ticket_id')
