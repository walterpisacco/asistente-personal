"""characters plan flags

Revision ID: 014
Revises: 013
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PLAN_COLUMNS = ("free_plan", "basic_plan", "premium_plan")


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _columns("characters")
    if not cols:
        return
    for name in _PLAN_COLUMNS:
        if name in cols:
            continue
        op.add_column(
            "characters",
            sa.Column(
                name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    cols = _columns("characters")
    for name in reversed(_PLAN_COLUMNS):
        if name in cols:
            op.drop_column("characters", name)
