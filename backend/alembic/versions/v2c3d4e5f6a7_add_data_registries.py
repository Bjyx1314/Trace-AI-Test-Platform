"""add data registries (schema/capability/scenario)

Revision ID: v2c3d4e5f6a7
Revises: u1b2c3d4e5f6
Create Date: 2026-07-21

测试数据准备与状态编排 MVP-1 基座：数据对象 Schema 注册表、数据能力、数据场景。
只建表 + CRUD/生命周期，暂不接执行引擎（Resolver/Orchestration 为下一增量）。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "v2c3d4e5f6a7"
down_revision: Union[str, None] = "u1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_data_object_schema",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("data_type", sa.String(length=60), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("schema_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("owner", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("data_type", "schema_version", name="uq_data_object_schema"),
    )
    op.create_index("ix_test_data_object_schema_data_type", "test_data_object_schema", ["data_type"])

    op.create_table(
        "test_data_capability",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("capability_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("provider_type", sa.String(length=20), nullable=False),
        sa.Column("business_domain", sa.String(length=60), nullable=True),
        sa.Column("executor_ref", sa.String(length=200), nullable=True),
        sa.Column("input_schema", JSONB(), nullable=True),
        sa.Column("parameter_mapping", JSONB(), nullable=True),
        sa.Column("output_extract", JSONB(), nullable=True),
        sa.Column("idempotency_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("sla_seconds", sa.Integer(), nullable=True),
        sa.Column("side_effects", JSONB(), nullable=True),
        sa.Column("cleanup_mode", sa.String(length=30), nullable=False, server_default="TTL"),
        sa.Column("cleanup_capability_id", sa.String(length=120), nullable=True),
        sa.Column("supports_strong_rollback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retention_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("supported_environments", JSONB(), nullable=True),
        sa.Column("owner", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("approval_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("last_verify", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_id", "version", name="uq_data_capability"),
    )
    op.create_index("ix_test_data_capability_capability_id", "test_data_capability", ["capability_id"])

    op.create_table(
        "test_data_scenario",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scenario_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=True),
        sa.Column("data_type", sa.String(length=60), nullable=True),
        sa.Column("provides", JSONB(), nullable=True),
        sa.Column("supported_schema_versions", JSONB(), nullable=True),
        sa.Column("supported_environments", JSONB(), nullable=True),
        sa.Column("supported_constraints", JSONB(), nullable=True),
        sa.Column("guarantees", JSONB(), nullable=True),
        sa.Column("workflow", JSONB(), nullable=True),
        sa.Column("postconditions", JSONB(), nullable=True),
        sa.Column("outputs", JSONB(), nullable=True),
        sa.Column("credentials", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("owner", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "version", name="uq_data_scenario"),
    )
    op.create_index("ix_test_data_scenario_scenario_id", "test_data_scenario", ["scenario_id"])


def downgrade() -> None:
    op.drop_index("ix_test_data_scenario_scenario_id", table_name="test_data_scenario")
    op.drop_table("test_data_scenario")
    op.drop_index("ix_test_data_capability_capability_id", table_name="test_data_capability")
    op.drop_table("test_data_capability")
    op.drop_index("ix_test_data_object_schema_data_type", table_name="test_data_object_schema")
    op.drop_table("test_data_object_schema")
