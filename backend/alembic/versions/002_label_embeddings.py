"""label embeddings table

Revision ID: 002
Revises: 001
Create Date: 2026-08-16
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
        "label_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_product", sa.String(64), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
    )
    op.create_index("ix_label_embeddings_id_product", "label_embeddings", ["id_product"])


def downgrade() -> None:
    op.drop_index("ix_label_embeddings_id_product", table_name="label_embeddings")
    op.drop_table("label_embeddings")
