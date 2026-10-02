from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.recordatory import Recordatory


class RecordatoryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        user_id: int,
        message: str,
        scheduled_at: datetime | None = None,
    ) -> Recordatory:
        row = Recordatory(
            user_id=int(user_id),
            message=message.strip()[:1000],
            scheduled_at=scheduled_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def find_active_duplicate(
        self,
        *,
        user_id: int,
        message: str,
        scheduled_at: datetime | None,
        within_minutes: int = 120,
    ) -> Recordatory | None:
        """Mismo mensaje + misma hora (±1 min), creado hace poco y no borrado."""
        msg = (message or "").strip()[:1000]
        if not msg:
            return None
        q = (
            self.db.query(Recordatory)
            .filter(
                Recordatory.user_id == int(user_id),
                Recordatory.deleted_at.is_(None),
                Recordatory.message == msg,
            )
        )
        if scheduled_at is not None:
            q = q.filter(
                Recordatory.scheduled_at >= scheduled_at - timedelta(minutes=1),
                Recordatory.scheduled_at <= scheduled_at + timedelta(minutes=1),
            )
        else:
            q = q.filter(Recordatory.scheduled_at.is_(None))
        if within_minutes > 0:
            since = datetime.utcnow() - timedelta(minutes=within_minutes)
            q = q.filter(Recordatory.created_at >= since)
        return q.order_by(Recordatory.id.desc()).first()

    def list_for_user(
        self,
        user_id: int,
        *,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[Recordatory]:
        q = self.db.query(Recordatory).filter(Recordatory.user_id == int(user_id))
        if not include_deleted:
            q = q.filter(Recordatory.deleted_at.is_(None))
        return (
            q.order_by(
                Recordatory.scheduled_at.is_(None),
                Recordatory.scheduled_at.asc(),
                Recordatory.id.asc(),
            )
            .limit(max(1, limit))
            .all()
        )
