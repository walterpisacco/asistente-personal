"""user.character_id, conversations.user_id, drop products

Revision ID: 005
Revises: 004
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return any(col["name"] == column for col in _inspector().get_columns(table))


def _drop_fk_on_column(table: str, column: str) -> None:
    if not _has_table(table):
        return
    for fk in _inspector().get_foreign_keys(table):
        if column in (fk.get("constrained_columns") or []):
            name = fk.get("name")
            if name:
                op.drop_constraint(name, table, type_="foreignkey")


def upgrade() -> None:
    if not _has_column("users", "character_id"):
        op.add_column(
            "users",
            sa.Column("character_id", sa.String(length=64), nullable=True),
        )
        op.create_index("ix_users_character_id", "users", ["character_id"])
        op.create_foreign_key(
            "fk_users_character_id",
            "users",
            "characters",
            ["character_id"],
            ["id"],
        )

    if not _has_column("conversations", "user_id"):
        op.add_column(
            "conversations",
            sa.Column("user_id", sa.String(length=64), nullable=True),
        )
        op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
        op.create_foreign_key(
            "fk_conversations_user_id",
            "conversations",
            "users",
            ["user_id"],
            ["id"],
        )

    if _has_column("conversations", "product_id"):
        _drop_fk_on_column("conversations", "product_id")
        op.drop_column("conversations", "product_id")

    if _has_table("label_embeddings"):
        op.drop_table("label_embeddings")
    if _has_table("product_detail"):
        op.drop_table("product_detail")

    if _has_table("products"):
        for index in _inspector().get_indexes("products"):
            name = index.get("name")
            if name:
                op.drop_index(name, table_name="products")
        op.drop_table("products")


def downgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("qr_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=120)),
        sa.Column("description", sa.Text()),
        sa.Column("image_url", sa.String(length=512)),
        sa.Column(
            "character_id",
            sa.String(length=64),
            sa.ForeignKey("characters.id"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_products_qr_code", "products", ["qr_code"], unique=True)

    op.create_table(
        "product_detail",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("precio_compra", sa.Numeric(12, 2)),
        sa.Column("precio_venta", sa.Numeric(12, 2)),
        sa.Column("stock_actual", sa.Integer(), server_default="0"),
        sa.Column("stock_min", sa.Integer(), server_default="0"),
        sa.Column("stock_max", sa.Integer(), server_default="0"),
        sa.Column("proveedor", sa.String(length=160)),
    )

    op.create_table(
        "label_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "id_product",
            sa.String(length=64),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column("embedding", sa.JSON(), nullable=False),
    )

    op.add_column(
        "conversations",
        sa.Column("product_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_product_id",
        "conversations",
        "products",
        ["product_id"],
        ["id"],
    )

    op.drop_constraint("fk_conversations_user_id", "conversations", type_="foreignkey")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_column("conversations", "user_id")

    op.drop_constraint("fk_users_character_id", "users", type_="foreignkey")
    op.drop_index("ix_users_character_id", table_name="users")
    op.drop_column("users", "character_id")
