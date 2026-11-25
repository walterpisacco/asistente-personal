"""tasks table

Revision ID: 011
Revises: 010
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "tasks" in tables:
        return
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "proyect_id",
            sa.Integer(),
            sa.ForeignKey("proyects.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(5000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tasks_proyect_id", "tasks", ["proyect_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "tasks" not in tables:
        return
    indexes = {ix["name"] for ix in inspect(bind).get_indexes("tasks")}
    if "ix_tasks_proyect_id" in indexes:
        op.drop_index("ix_tasks_proyect_id", table_name="tasks")
    op.drop_table("tasks")
