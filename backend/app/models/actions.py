from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Action(Base):
    """Acción que el LLM puede emitir vía {"clave","valor"} → método del backend."""

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    clave: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    # Valor de ejemplo / hint opcional (el valor runtime lo manda el LLM).
    valor: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # Nombre del handler registrado en ActionDispatcher (ej. ver_video).
    metodo: Mapped[str] = mapped_column(String(120), nullable=False)
