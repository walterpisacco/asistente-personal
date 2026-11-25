"""product_detail table

Revision ID: 003
Revises: 002
Create Date: 2026-09-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_detail",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            sa.String(64),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("precio_compra", sa.Numeric(12, 2), nullable=True),
        sa.Column("precio_venta", sa.Numeric(12, 2), nullable=True),
        sa.Column("stock_actual", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stock_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stock_max", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proveedor", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_product_detail_product_id",
        "product_detail",
        ["product_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_product_detail_product_id", table_name="product_detail")
    op.drop_table("product_detail")
