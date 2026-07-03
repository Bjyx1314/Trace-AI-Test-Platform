"""add testcase soft delete and audit log table

Revision ID: d4e8f3a9c2b1
Revises: c3e7f2a9d1b5
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd4e8f3a9c2b1'
down_revision: Union[str, None] = 'c3e7f2a9d1b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_cases', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    op.create_table(
        'test_case_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('test_case_id', sa.String(36), nullable=False),
        sa.Column('operation', sa.String(20), nullable=False),
        sa.Column('operator', sa.String(100), nullable=False, server_default='系统'),
        sa.Column('snapshot', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
    )
    op.create_index('ix_test_case_logs_test_case_id', 'test_case_logs', ['test_case_id'])


def downgrade() -> None:
    op.drop_index('ix_test_case_logs_test_case_id', table_name='test_case_logs')
    op.drop_table('test_case_logs')
    op.drop_column('test_cases', 'deleted_at')
