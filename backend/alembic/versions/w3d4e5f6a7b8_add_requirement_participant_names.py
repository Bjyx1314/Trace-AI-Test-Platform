"""add requirement participant names

Revision ID: w3d4e5f6a7b8
Revises: v2c3d4e5f6a7
Create Date: 2026-07-26
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "w3d4e5f6a7b8"
down_revision: Union[str, None] = "v2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column(
            "participant_names",
            postgresql.ARRAY(sa.String(length=200)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.execute(
        """
        UPDATE requirements
        SET participant_names = ARRAY[owner_name]
        WHERE owner_name IS NOT NULL
          AND cardinality(participant_names) = 0
        """
    )


def downgrade() -> None:
    op.drop_column("requirements", "participant_names")
