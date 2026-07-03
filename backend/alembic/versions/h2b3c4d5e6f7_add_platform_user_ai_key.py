"""add ai_api_key to platform_users (per-user AI key)

Revision ID: h2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h2b3c4d5e6f7'
down_revision: Union[str, None] = 'g1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('platform_users', sa.Column('ai_api_key', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('platform_users', 'ai_api_key')
