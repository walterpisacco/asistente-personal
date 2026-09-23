from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserEmbedding(Base):
    """Embedding de la voz (y opcionalmente rostro) de un usuario."""

    __tablename__ = "user_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_user: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    embedding_voice: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    embedding_face: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)

    user = relationship("User", back_populates="user_embeddings")
