"""proyects table

Revision ID: 010
Revises: 009
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "proyects" in tables:
        return
    op.create_table(
        "proyects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_proyects_user_id", "proyects", ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "proyects" not in tables:
        return
    indexes = {ix["name"] for ix in inspect(bind).get_indexes("proyects")}
    if "ix_proyects_user_id" in indexes:
        op.drop_index("ix_proyects_user_id", table_name="proyects")
    op.drop_table("proyects")
