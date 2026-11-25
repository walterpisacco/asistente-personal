from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.avatars import Avatar


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    voice_provider: Mapped[str] = mapped_column(String(64), default="deepgram")
    voice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    animation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    free_plan: Mapped[bool] = mapped_column(Boolean, default=False)
    basic_plan: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_plan: Mapped[bool] = mapped_column(Boolean, default=False)

    users = relationship("User", back_populates="character")
    conversations = relationship("Conversation", back_populates="character")
    avatars: Mapped[list["Avatar"]] = relationship(
        "Avatar",
        back_populates="character",
        order_by="Avatar.id",
        cascade="all, delete-orphan",
    )

