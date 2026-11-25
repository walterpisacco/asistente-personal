from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.conversation_message import ConversationMessage
    from app.models.tasks import Tasks
    from app.models.user import User


class Proyects(Base):
    """Proyecto del usuario."""

    __tablename__ = "proyects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="proyects")
    tasks: Mapped[list["Tasks"]] = relationship(
        "Tasks",
        back_populates="proyect",
        cascade="all, delete-orphan",
    )
    conversation_messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="proyect",
    )
