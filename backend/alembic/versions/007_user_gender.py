"""users.gender

Revision ID: 007
Revises: 006
Create Date: 2026-10-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("users", "gender"):
        op.add_column("users", sa.Column("gender", sa.String(length=32), nullable=True))


def downgrade() -> None:
    if _has_column("users", "gender"):
        op.drop_column("users", "gender")
