"""shift existing naive timestamps from UTC to CST (+8h)

历史数据是在容器 UTC 时区下用 func.now()/utcnow() 落库的(naive)，比中国本地时间慢 8 小时。
平台全程单一中国本地时区，无多时区需求，故把所有已存在的 timestamp 列统一 +8 小时校正为 CST。
此迁移幂等性由 alembic 版本表保证(每库只跑一次)；新库无数据则 no-op。

Revision ID: j4d5e6f7a8b9
Revises: i3c4d5e6f7a8
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j4d5e6f7a8b9'
down_revision: Union[str, None] = 'i3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type IN ('timestamp without time zone', 'timestamp with time zone')
          AND table_name <> 'alembic_version'
        """
    )).fetchall()
    for table_name, column_name in rows:
        conn.execute(sa.text(
            f'UPDATE "{table_name}" SET "{column_name}" = "{column_name}" + interval \'8 hours\' '
            f'WHERE "{column_name}" IS NOT NULL'
        ))


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type IN ('timestamp without time zone', 'timestamp with time zone')
          AND table_name <> 'alembic_version'
        """
    )).fetchall()
    for table_name, column_name in rows:
        conn.execute(sa.text(
            f'UPDATE "{table_name}" SET "{column_name}" = "{column_name}" - interval \'8 hours\' '
            f'WHERE "{column_name}" IS NOT NULL'
        ))
