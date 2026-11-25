"""actions + recordatory

Revision ID: 006
Revises: 005
Create Date: 2026-10-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "actions" not in tables:
        op.create_table(
            "actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("clave", sa.String(120), nullable=False),
            sa.Column("valor", sa.String(500), nullable=False, server_default=""),
            sa.Column("metodo", sa.String(120), nullable=False),
        )
        op.create_index("ix_actions_clave", "actions", ["clave"], unique=True)

    if "recordatory" not in tables:
        op.create_table(
            "recordatory",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("message", sa.String(1000), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_recordatory_user_id", "recordatory", ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "recordatory" in tables:
        indexes = {ix["name"] for ix in inspect(bind).get_indexes("recordatory")}
        if "ix_recordatory_user_id" in indexes:
            op.drop_index("ix_recordatory_user_id", table_name="recordatory")
        op.drop_table("recordatory")
    if "actions" in tables:
        indexes = {ix["name"] for ix in inspect(bind).get_indexes("actions")}
        if "ix_actions_clave" in indexes:
            op.drop_index("ix_actions_clave", table_name="actions")
        op.drop_table("actions")
