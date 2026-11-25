from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.conversation_message import ConversationMessage
from app.models.proyects import Proyects
from app.models.tasks import Tasks

DEFAULT_PROJECT_NAME = "Temas en General"


def is_default_project_name(name: str) -> bool:
    return (name or "").strip().lower() == DEFAULT_PROJECT_NAME.lower()


class ProyectsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        user_id: int,
        name: str,
    ) -> Proyects:
        now = datetime.utcnow()
        row = Proyects(
            user_id=int(user_id),
            name=(name or "").strip()[:500],
            created_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def ensure_default_for_user(self, user_id: int) -> Proyects:
        existing = self.find_active_duplicate(
            user_id=int(user_id),
            name=DEFAULT_PROJECT_NAME,
        )
        if existing is not None:
            return existing
        return self.create(user_id=int(user_id), name=DEFAULT_PROJECT_NAME)

    def find_active_duplicate(
        self,
        *,
        user_id: int,
        name: str,
    ) -> Proyects | None:
        cleaned = (name or "").strip()[:500]
        if not cleaned:
            return None
        return (
            self.db.query(Proyects)
            .filter(
                Proyects.user_id == int(user_id),
                func.lower(Proyects.name) == cleaned.lower(),
                Proyects.deleted_at.is_(None),
            )
            .order_by(Proyects.id.desc())
            .first()
        )

    def list_for_user(
        self,
        user_id: int,
        *,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[Proyects]:
        q = self.db.query(Proyects).filter(Proyects.user_id == int(user_id))
        if not include_deleted:
            q = q.filter(Proyects.deleted_at.is_(None))
        return (
            q.order_by(Proyects.id.asc())
            .limit(max(1, limit))
            .all()
        )

    def get_active_for_user(self, user_id: int, proyect_id: int) -> Proyects | None:
        return (
            self.db.query(Proyects)
            .filter(
                Proyects.id == int(proyect_id),
                Proyects.user_id == int(user_id),
                Proyects.deleted_at.is_(None),
            )
            .first()
        )

    def count_related(self, proyect_id: int) -> tuple[int, int]:
        pid = int(proyect_id)
        tasks = (
            self.db.query(func.count(Tasks.id))
            .filter(Tasks.proyect_id == pid)
            .scalar()
            or 0
        )
        messages = (
            self.db.query(func.count(ConversationMessage.id))
            .filter(ConversationMessage.proyect_id == pid)
            .scalar()
            or 0
        )
        return int(tasks), int(messages)

    def delete(self, row: Proyects) -> dict[str, int | str]:
        if is_default_project_name(row.name):
            raise ValueError("default_project_protected")
        proyect_id = int(row.id)
        name = row.name
        tasks_deleted = (
            self.db.query(Tasks)
            .filter(Tasks.proyect_id == proyect_id)
            .delete(synchronize_session=False)
        )
        messages_deleted = (
            self.db.query(ConversationMessage)
            .filter(ConversationMessage.proyect_id == proyect_id)
            .delete(synchronize_session=False)
        )
        self.db.delete(row)
        self.db.commit()
        return {
            "id": proyect_id,
            "name": name,
            "tasks": int(tasks_deleted or 0),
            "messages": int(messages_deleted or 0),
        }
