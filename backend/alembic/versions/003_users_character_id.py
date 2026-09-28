"""users.character_id → characters.id

Revision ID: 003
Revises: 002
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("users")}
    fks = {fk["name"] for fk in inspect(bind).get_foreign_keys("users")}
    indexes = {ix["name"] for ix in inspect(bind).get_indexes("users")}

    if "character_id" not in cols:
        op.add_column(
            "users",
            sa.Column("character_id", sa.String(64), nullable=True),
        )

    bind.execute(
        text(
            """
            UPDATE users
            SET character_id = (SELECT id FROM characters ORDER BY id LIMIT 1)
            WHERE character_id IS NULL
              AND EXISTS (SELECT 1 FROM characters LIMIT 1)
            """
        )
    )

    # MySQL: ALTER nullable → NOT NULL
    op.alter_column(
        "users",
        "character_id",
        existing_type=sa.String(64),
        nullable=False,
    )

    if "fk_users_character_id" not in fks:
        op.create_foreign_key(
            "fk_users_character_id",
            "users",
            "characters",
            ["character_id"],
            ["id"],
        )
    if "ix_users_character_id" not in indexes:
        op.create_index("ix_users_character_id", "users", ["character_id"])


def downgrade() -> None:
    bind = op.get_bind()
    fks = {fk["name"] for fk in inspect(bind).get_foreign_keys("users")}
    indexes = {ix["name"] for ix in inspect(bind).get_indexes("users")}
    cols = {c["name"] for c in inspect(bind).get_columns("users")}

    if "ix_users_character_id" in indexes:
        op.drop_index("ix_users_character_id", table_name="users")
    if "fk_users_character_id" in fks:
        op.drop_constraint("fk_users_character_id", "users", type_="foreignkey")
    if "character_id" in cols:
        op.drop_column("users", "character_id")
