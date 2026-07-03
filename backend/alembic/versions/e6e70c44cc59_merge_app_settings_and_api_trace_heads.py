"""merge app_settings and api_trace heads

Revision ID: e6e70c44cc59
Revises: a9d1f0c3b5e7, e3f4a5b6c7d8
Create Date: 2026-06-24 22:23:43.716419

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6e70c44cc59'
down_revision: Union[str, None] = ('a9d1f0c3b5e7', 'e3f4a5b6c7d8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
