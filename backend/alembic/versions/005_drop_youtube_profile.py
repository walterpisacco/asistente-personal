"""drop users.youtube_profile

Revision ID: 005
Revises: 004
Create Date: 2026-10-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("users")}
    if "youtube_profile" in cols:
        op.drop_column("users", "youtube_profile")


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("users")}
    if "youtube_profile" not in cols:
        op.add_column(
            "users",
            sa.Column("youtube_profile", sa.String(160), nullable=True),
        )
