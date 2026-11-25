"""recordatory recurrence fields

Revision ID: 016
Revises: 015
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _columns("recordatory")
    if not cols:
        return
    if "recurrence" not in cols:
        op.add_column(
            "recordatory",
            sa.Column(
                "recurrence",
                sa.String(32),
                nullable=False,
                server_default="once",
            ),
        )
    if "time_of_day" not in cols:
        op.add_column(
            "recordatory",
            sa.Column("time_of_day", sa.String(5), nullable=True),
        )
    if "weekdays" not in cols:
        op.add_column(
            "recordatory",
            sa.Column("weekdays", sa.String(32), nullable=True),
        )


def downgrade() -> None:
    cols = _columns("recordatory")
    if "weekdays" in cols:
        op.drop_column("recordatory", "weekdays")
    if "time_of_day" in cols:
        op.drop_column("recordatory", "time_of_day")
    if "recurrence" in cols:
        op.drop_column("recordatory", "recurrence")
