"""conversation_messages.proyect_id

Revision ID: 012
Revises: 011
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "conversation_messages" not in tables:
        return
    columns = {
        col["name"] for col in inspect(bind).get_columns("conversation_messages")
    }
    if "proyect_id" in columns:
        return
    op.add_column(
        "conversation_messages",
        sa.Column("proyect_id", sa.Integer(), sa.ForeignKey("proyects.id"), nullable=True),
    )
    op.create_index(
        "ix_conversation_messages_proyect_id",
        "conversation_messages",
        ["proyect_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "conversation_messages" not in tables:
        return
    indexes = {
        ix["name"] for ix in inspect(bind).get_indexes("conversation_messages")
    }
    if "ix_conversation_messages_proyect_id" in indexes:
        op.drop_index(
            "ix_conversation_messages_proyect_id",
            table_name="conversation_messages",
        )
    columns = {
        col["name"] for col in inspect(bind).get_columns("conversation_messages")
    }
    if "proyect_id" in columns:
        op.drop_column("conversation_messages", "proyect_id")
