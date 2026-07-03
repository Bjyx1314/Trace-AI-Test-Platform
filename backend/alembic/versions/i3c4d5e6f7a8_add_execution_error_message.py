"""add error_message to executions (batch-level failure reason)

Revision ID: i3c4d5e6f7a8
Revises: h2b3c4d5e6f7
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i3c4d5e6f7a8'
down_revision: Union[str, None] = 'h2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('executions', sa.Column('error_message', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('executions', 'error_message')
