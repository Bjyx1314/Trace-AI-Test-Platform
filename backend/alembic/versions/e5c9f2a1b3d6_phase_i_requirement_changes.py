"""Phase I: add requirement attachment_path and migrate status lifecycle

Revision ID: e5c9f2a1b3d6
Revises: b2f6d4e8a1c3
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c9f2a1b3d6'
down_revision: Union[str, None] = 'b2f6d4e8a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('requirements', sa.Column('attachment_path', sa.String(length=500), nullable=True))

    # Migrate old status values to new 7-value lifecycle
    op.execute("""
        UPDATE requirements
        SET status = CASE
            WHEN status = 'pending'   THEN 'pending_analysis'
            WHEN status = 'analyzed'  THEN 'pending_case_generation'
            WHEN status = 'done'      THEN 'pending_test'
            WHEN status = 'failed'    THEN 'pending_analysis'
            ELSE status
        END
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE requirements
        SET status = CASE
            WHEN status = 'pending_analysis'        THEN 'pending'
            WHEN status = 'pending_case_generation' THEN 'analyzed'
            WHEN status = 'generating_cases'        THEN 'analyzing'
            WHEN status = 'pending_test'            THEN 'done'
            WHEN status = 'testing'                 THEN 'done'
            ELSE status
        END
    """)
    op.drop_column('requirements', 'attachment_path')
