from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Recordatory(Base):
    """Recordatorio programado para un usuario.

    recurrence:
      - once: un solo disparo en scheduled_at
      - daily: todos los días a time_of_day
      - weekly: días en weekdays (ISO 1=lun … 7=dom)
      - weekends: sábados y domingos (weekdays=6,7)
    scheduled_at siempre guarda la próxima ocurrencia a notificar.
    """

    __tablename__ = "recordatory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    state: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    recurrence: Mapped[str] = mapped_column(
        String(32), nullable=False, default="once", server_default="once"
    )
    time_of_day: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    weekdays: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="recordatories")
