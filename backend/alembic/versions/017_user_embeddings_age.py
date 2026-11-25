"""user_embeddings table and users.age for bot local

Revision ID: 017
Revises: 016
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    user_cols = {c["name"] for c in inspect(bind).get_columns("users")}

    if "age" not in user_cols:
        op.add_column("users", sa.Column("age", sa.Integer(), nullable=True))

    if "user_embeddings" not in tables:
        op.create_table(
            "user_embeddings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "id_user",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("embedding_voice", sa.JSON(), nullable=False),
            sa.Column("embedding_face", sa.JSON(), nullable=True),
        )
        op.create_index("ix_user_embeddings_id_user", "user_embeddings", ["id_user"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    user_cols = {c["name"] for c in inspect(bind).get_columns("users")}

    if "user_embeddings" in tables:
        op.drop_index("ix_user_embeddings_id_user", table_name="user_embeddings")
        op.drop_table("user_embeddings")

    if "age" in user_cols:
        op.drop_column("users", "age")
