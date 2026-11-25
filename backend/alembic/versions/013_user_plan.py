"""users.plan_clave and memory window

Revision ID: 013
Revises: 012
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    user_cols = _columns("users")
    if "plan_clave" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "plan_clave",
                sa.String(32),
                nullable=False,
                server_default="free",
            ),
        )
    if "plan_started_at" not in user_cols:
        op.add_column(
            "users",
            sa.Column("plan_started_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE users SET plan_started_at = created_at "
                "WHERE plan_started_at IS NULL"
            )
        )

    msg_cols = _columns("conversation_messages")
    msg_indexes = _indexes("conversation_messages")
    if (
        "created_at" in msg_cols
        and "ix_conversation_messages_created_at" not in msg_indexes
    ):
        op.create_index(
            "ix_conversation_messages_created_at",
            "conversation_messages",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    msg_indexes = _indexes("conversation_messages")
    if "ix_conversation_messages_created_at" in msg_indexes:
        op.drop_index(
            "ix_conversation_messages_created_at",
            table_name="conversation_messages",
        )
    user_cols = _columns("users")
    if "plan_started_at" in user_cols:
        op.drop_column("users", "plan_started_at")
    if "plan_clave" in user_cols:
        op.drop_column("users", "plan_clave")
