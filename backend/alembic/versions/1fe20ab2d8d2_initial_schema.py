"""initial schema

Revision ID: 1fe20ab2d8d2
Revises:
Create Date: 2026-06-12 16:58:51.480638

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1fe20ab2d8d2'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('product_line', sa.String(length=50), nullable=True),
        sa.Column('case_id_prefix', sa.String(length=20), nullable=False),
        sa.Column('feishu_webhook', sa.String(length=500), nullable=True),
        sa.Column('feishu_doc_url', sa.String(length=500), nullable=True),
        sa.Column('ci_gate_enabled', sa.Boolean(), nullable=False),
        sa.Column('pass_rate_threshold', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'enum_definitions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('parent_key', sa.String(length=100), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('category', 'key', name='uq_enum_category_key'),
    )

    op.create_table(
        'requirements',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('product_line', sa.String(length=50), nullable=True),
        sa.Column('source', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('analysis_result', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'test_cases',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('case_id', sa.String(length=50), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('requirement_id', sa.String(), nullable=True),
        sa.Column('product_line', sa.String(length=50), nullable=True),
        sa.Column('source_req_id', sa.String(length=50), nullable=True),
        sa.Column('modules', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('platforms', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('preconditions', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('steps', postgresql.JSONB(), nullable=False),
        sa.Column('expected_result', sa.Text(), nullable=True),
        sa.Column('source_issue_point', sa.String(length=50), nullable=True),
        sa.Column('case_type', sa.String(length=20), nullable=False),
        sa.Column('last_status', sa.String(length=20), nullable=False),
        sa.Column('script', sa.Text(), nullable=True),
        sa.Column('script_path', sa.String(length=255), nullable=True),
        sa.Column('script_status', sa.String(length=30), nullable=False),
        sa.Column('tags', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_test_cases_case_id', 'test_cases', ['case_id'], unique=True)

    op.create_table(
        'executions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('trigger', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('passed', sa.Integer(), nullable=False),
        sa.Column('failed', sa.Integer(), nullable=False),
        sa.Column('skipped', sa.Integer(), nullable=False),
        sa.Column('pass_rate', sa.Float(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('ci_gate_result', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'quality_gate_configs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('overall_pass_rate_threshold', sa.Float(), nullable=False),
        sa.Column('enable_overall_pass_rate_gate', sa.Boolean(), nullable=False),
        sa.Column('p1_failure_threshold', sa.Integer(), nullable=False),
        sa.Column('enable_p1_failure_gate', sa.Boolean(), nullable=False),
        sa.Column('pass_rate_wow_drop_threshold', sa.Float(), nullable=False),
        sa.Column('coverage_threshold', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id'),
    )

    op.create_table(
        'test_results',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('execution_id', sa.String(), nullable=False),
        sa.Column('test_case_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('screenshot_url', sa.String(length=500), nullable=True),
        sa.Column('failure_type', sa.String(length=20), nullable=True),
        sa.Column('ai_diagnosis', postgresql.JSONB(), nullable=True),
        sa.Column('repair_suggestion', sa.Text(), nullable=True),
        sa.Column('defect_status', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['execution_id'], ['executions.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'defects',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('test_result_id', sa.String(), nullable=False),
        sa.Column('execution_id', sa.String(), nullable=False),
        sa.Column('test_case_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('confidence', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('draft_ticket', postgresql.JSONB(), nullable=True),
        sa.Column('feishu_ticket_id', sa.String(length=100), nullable=True),
        sa.Column('duplicate_of_defect_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['test_result_id'], ['test_results.id']),
        sa.ForeignKeyConstraint(['execution_id'], ['executions.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id']),
        sa.ForeignKeyConstraint(['duplicate_of_defect_id'], ['defects.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('defects')
    op.drop_table('test_results')
    op.drop_table('quality_gate_configs')
    op.drop_table('executions')
    op.drop_index('ix_test_cases_case_id', table_name='test_cases')
    op.drop_table('test_cases')
    op.drop_table('requirements')
    op.drop_table('enum_definitions')
    op.drop_table('projects')
