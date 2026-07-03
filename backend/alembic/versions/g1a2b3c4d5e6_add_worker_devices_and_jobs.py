"""add mobile_devices and app_exec_jobs (App真机执行机 worker 模型)

Revision ID: g1a2b3c4d5e6
Revises: f7a1b2c3d4e5
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'g1a2b3c4d5e6'
down_revision: Union[str, None] = 'f7a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mobile_devices',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('worker_id', sa.String(length=100), nullable=False),
        sa.Column('worker_name', sa.String(length=200), nullable=True),
        sa.Column('serial', sa.String(length=200), nullable=False),
        sa.Column('model', sa.String(length=200), nullable=True),
        sa.Column('owner_user_id', sa.String(length=36), nullable=True),
        sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('online', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('busy', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('last_seen', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('worker_id', 'serial', name='uq_mobile_device_worker_serial'),
    )
    op.create_index('ix_mobile_devices_worker_id', 'mobile_devices', ['worker_id'])
    op.create_index('ix_mobile_devices_serial', 'mobile_devices', ['serial'])

    op.create_table(
        'app_exec_jobs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('execution_id', sa.String(length=36), nullable=False),
        sa.Column('test_case_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=True),
        sa.Column('target_serial', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('claimed_worker', sa.String(length=100), nullable=True),
        sa.Column('claimed_serial', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['execution_id'], ['executions.id'], ),
        sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_app_exec_jobs_execution_id', 'app_exec_jobs', ['execution_id'])
    op.create_index('ix_app_exec_jobs_target_serial', 'app_exec_jobs', ['target_serial'])
    op.create_index('ix_app_exec_jobs_status', 'app_exec_jobs', ['status'])


def downgrade() -> None:
    op.drop_index('ix_app_exec_jobs_status', table_name='app_exec_jobs')
    op.drop_index('ix_app_exec_jobs_target_serial', table_name='app_exec_jobs')
    op.drop_index('ix_app_exec_jobs_execution_id', table_name='app_exec_jobs')
    op.drop_table('app_exec_jobs')
    op.drop_index('ix_mobile_devices_serial', table_name='mobile_devices')
    op.drop_index('ix_mobile_devices_worker_id', table_name='mobile_devices')
    op.drop_table('mobile_devices')
