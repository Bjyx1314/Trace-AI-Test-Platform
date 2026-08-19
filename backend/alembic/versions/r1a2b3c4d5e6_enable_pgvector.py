"""enable pgvector extension (AI质量闭环完整版: 经验/覆盖项语义检索地基)

需 postgres 镜像为 pgvector/pgvector:pg16（或已装 vector 扩展）。
本迁移只建扩展，向量列由后续阶段迁移添加。

Revision ID: r1a2b3c4d5e6
Revises: q6f7a8b9c0d1
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'r1a2b3c4d5e6'
down_revision: Union[str, None] = 'q6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # 不 DROP EXTENSION：可能有其它对象依赖，且重复启用无害
    pass
