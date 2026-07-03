"""add enum_logs table

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'enum_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('category', sa.String(100), nullable=False, index=True),
        sa.Column('enum_id', sa.String(36), nullable=True),
        sa.Column('operation', sa.String(20), nullable=False),
        sa.Column('value', sa.String(200), nullable=True),
        sa.Column('operator', sa.String(100), nullable=False, server_default='系统'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('enum_logs')
