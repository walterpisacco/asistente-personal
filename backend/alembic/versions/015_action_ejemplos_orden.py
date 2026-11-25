"""actions ejemplos + orden

Revision ID: 015
Revises: 014
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _columns("actions")
    if not cols:
        return
    if "ejemplos" not in cols:
        op.add_column("actions", sa.Column("ejemplos", sa.String(100), nullable=True))
    if "orden" not in cols:
        op.add_column("actions", sa.Column("orden", sa.Integer(), nullable=True))


def downgrade() -> None:
    cols = _columns("actions")
    if "orden" in cols:
        op.drop_column("actions", "orden")
    if "ejemplos" in cols:
        op.drop_column("actions", "ejemplos")
