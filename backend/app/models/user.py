from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="operator")
    character_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("characters.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    plan_clave: Mapped[str] = mapped_column(
        String(32), nullable=False, default="free", server_default="free"
    )
    plan_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    character = relationship("Character", back_populates="users")
    conversations = relationship("Conversation", back_populates="user")
    recordatories = relationship(
        "Recordatory",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    proyects = relationship(
        "Proyects",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    user_profile = relationship(
        "UserProfile",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    user_embeddings = relationship(
        "UserEmbedding",
        back_populates="user",
        cascade="all, delete-orphan",
    )
