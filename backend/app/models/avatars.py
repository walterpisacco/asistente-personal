from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

class Avatar(Base):
    """Avatar para un personaje."""

    __tablename__ = "avatars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("characters.id"), nullable=False, index=True
    )
    avatar_url: Mapped[str] = mapped_column(String(512), nullable=False)

    character: Mapped["Character"] = relationship("Character", back_populates="avatars")