"""initial schema — actions

Revision ID: 002
Revises: 001
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("clave", sa.String(120), nullable=False),
        sa.Column("valor", sa.String(500), nullable=False, server_default=""),
        sa.Column("metodo", sa.String(120), nullable=False),
    )
    op.create_index("ix_actions_clave", "actions", ["clave"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_actions_clave", table_name="actions")
    op.drop_table("actions")
