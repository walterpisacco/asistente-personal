"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("voice_provider", sa.String(64), nullable=True),
        sa.Column("voice_id", sa.String(128), nullable=False),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("animation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("qr_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("brand", sa.String(120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(512), nullable=True),
        sa.Column("character_id", sa.String(64), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("knowledge_base_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_products_qr_code", "products", ["qr_code"], unique=True)
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("character_id", sa.String(64), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("device_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(64), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("audio_url", sa.String(512), nullable=True),
        sa.Column("latency_ms", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"])
    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=True),
        sa.Column("character_id", sa.String(64), nullable=True),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("device_id", sa.String(128), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_interactions_event", "interactions", ["event"])


def downgrade() -> None:
    op.drop_table("interactions")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")
    op.drop_table("products")
    op.drop_table("characters")
