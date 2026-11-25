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
        recurrence: str = "once",
        time_of_day: str | None = None,
        weekdays: str | None = None,
    ) -> Recordatory:
        now = datetime.utcnow()
        row = Recordatory(
            user_id=int(user_id),
            message=message.strip()[:1000],
            scheduled_at=scheduled_at,
            created_at=now,
            updated_at=now,
            state=1,
            recurrence=(recurrence or "once").strip().lower() or "once",
            time_of_day=(time_of_day or None),
            weekdays=(weekdays or None),
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
        recurrence: str = "once",
        time_of_day: str | None = None,
        weekdays: str | None = None,
        within_minutes: int = 120,
    ) -> Recordatory | None:
        """Mismo mensaje + misma programación, activo y no borrado."""
        msg = (message or "").strip()[:1000]
        if not msg:
            return None
        rec = (recurrence or "once").strip().lower() or "once"
        q = (
            self.db.query(Recordatory)
            .filter(
                Recordatory.user_id == int(user_id),
                Recordatory.deleted_at.is_(None),
                Recordatory.state == 1,
                Recordatory.message == msg,
                Recordatory.recurrence == rec,
            )
        )
        if rec == "once":
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
        else:
            if time_of_day:
                q = q.filter(Recordatory.time_of_day == time_of_day)
            if weekdays:
                q = q.filter(Recordatory.weekdays == weekdays)
            elif rec != "daily":
                q = q.filter(
                    (Recordatory.weekdays.is_(None)) | (Recordatory.weekdays == "")
                )
        return q.order_by(Recordatory.id.desc()).first()

    def list_for_user(
        self,
        user_id: int,
        *,
        include_deleted: bool = False,
        include_inactive: bool = False,
        limit: int = 20,
    ) -> list[Recordatory]:
        q = self.db.query(Recordatory).filter(Recordatory.user_id == int(user_id))
        if not include_deleted:
            q = q.filter(Recordatory.deleted_at.is_(None))
        if not include_inactive:
            q = q.filter(Recordatory.state == 1)
        return (
            q.order_by(
                Recordatory.scheduled_at.is_(None),
                Recordatory.scheduled_at.asc(),
                Recordatory.id.asc(),
            )
            .limit(max(1, limit))
            .all()
        )

    def get_active_for_user(self, user_id: int, recordatory_id: int) -> Recordatory | None:
        return (
            self.db.query(Recordatory)
            .filter(
                Recordatory.id == int(recordatory_id),
                Recordatory.user_id == int(user_id),
                Recordatory.deleted_at.is_(None),
            )
            .first()
        )

    def soft_delete(self, row: Recordatory) -> Recordatory:
        now = datetime.utcnow()
        row.deleted_at = now
        row.updated_at = now
        row.state = 0
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def save(self, row: Recordatory) -> Recordatory:
        row.updated_at = datetime.utcnow()
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row
