from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.proyects import Proyects
from app.models.tasks import Tasks


class TasksRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        proyect_id: int,
        name: str,
    ) -> Tasks:
        now = datetime.utcnow()
        row = Tasks(
            proyect_id=int(proyect_id),
            name=(name or "").strip()[:5000],
            created_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def find_active_duplicate(
        self,
        *,
        proyect_id: int,
        name: str,
    ) -> Tasks | None:
        cleaned = (name or "").strip()[:5000]
        if not cleaned:
            return None
        return (
            self.db.query(Tasks)
            .filter(
                Tasks.proyect_id == int(proyect_id),
                func.lower(Tasks.name) == cleaned.lower(),
                Tasks.deleted_at.is_(None),
            )
            .order_by(Tasks.id.desc())
            .first()
        )

    def list_for_project(
        self,
        user_id: int,
        proyect_id: int,
        *,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[Tasks]:
        q = (
            self.db.query(Tasks)
            .join(Proyects, Tasks.proyect_id == Proyects.id)
            .filter(
                Proyects.user_id == int(user_id),
                Tasks.proyect_id == int(proyect_id),
            )
        )
        if not include_deleted:
            q = q.filter(Tasks.deleted_at.is_(None), Proyects.deleted_at.is_(None))
        return (
            q.order_by(Tasks.id.asc())
            .limit(max(1, limit))
            .all()
        )

    def get_active_for_project(
        self,
        user_id: int,
        proyect_id: int,
        task_id: int,
    ) -> Tasks | None:
        return (
            self.db.query(Tasks)
            .join(Proyects, Tasks.proyect_id == Proyects.id)
            .filter(
                Proyects.user_id == int(user_id),
                Tasks.proyect_id == int(proyect_id),
                Tasks.id == int(task_id),
                Tasks.deleted_at.is_(None),
                Proyects.deleted_at.is_(None),
            )
            .first()
        )

    def delete(self, row: Tasks) -> None:
        self.db.delete(row)
        self.db.commit()
