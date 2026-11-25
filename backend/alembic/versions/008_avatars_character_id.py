"""avatars.character_id as character id string

Revision ID: 008
Revises: 007
Create Date: 2026-10-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_fk(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return any(fk.get("name") == name for fk in _inspector().get_foreign_keys(table))


def upgrade() -> None:
    if not _has_table("avatars"):
        op.create_table(
            "avatars",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("character_id", sa.String(64), nullable=False),
            sa.Column("avatar_url", sa.String(512), nullable=False),
            sa.ForeignKeyConstraint(
                ["character_id"],
                ["characters.id"],
                name="fk_avatars_character_id",
            ),
        )
        op.create_index("ix_avatars_character_id", "avatars", ["character_id"])
        return

    col = next(c for c in _inspector().get_columns("avatars") if c["name"] == "character_id")
    if not isinstance(col["type"], sa.String):
        op.alter_column(
            "avatars",
            "character_id",
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    # Los ids numéricos no existen en characters.id (string).
    op.execute(
        """
        UPDATE avatars
        SET character_id = CASE character_id
            WHEN '1' THEN 'avatar_female'
            WHEN '2' THEN 'avatar_male'
            WHEN '3' THEN 'avatar_traductor'
            ELSE character_id
        END
        """
    )

    op.execute(
        "ALTER TABLE avatars MODIFY character_id VARCHAR(64) "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL"
    )

    if not _has_fk("avatars", "fk_avatars_character_id"):
        op.create_foreign_key(
            "fk_avatars_character_id",
            "avatars",
            "characters",
            ["character_id"],
            ["id"],
        )


def downgrade() -> None:
    if _has_fk("avatars", "fk_avatars_character_id"):
        op.drop_constraint("fk_avatars_character_id", "avatars", type_="foreignkey")
