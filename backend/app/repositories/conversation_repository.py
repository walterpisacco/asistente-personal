from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.core.plans import DEFAULT_PLAN, PLAN_MEMORY_DAYS
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.user import User


class ConversationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_by_id(self, conversation_id: str) -> Optional[Conversation]:
        return (
            self.db.query(Conversation)
            .options(joinedload(Conversation.messages))
            .filter(Conversation.id == conversation_id)
            .first()
        )

    def list_messages(
        self,
        conversation_id: str,
        proyect_id: int | None = None,
    ) -> list[ConversationMessage]:
        """Lee mensajes frescos de DB (no usa la colección cacheada de la Session)."""
        q = self.db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id
        )
        if proyect_id is not None:
            q = q.filter(ConversationMessage.proyect_id == int(proyect_id))
        return q.order_by(ConversationMessage.id.asc()).all()

    def list_messages_for_user_proyect(
        self,
        user_id: int,
        proyect_id: int,
        *,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[ConversationMessage]:
        q = (
            self.db.query(ConversationMessage)
            .join(
                Conversation,
                ConversationMessage.conversation_id == Conversation.id,
            )
            .filter(
                Conversation.user_id == int(user_id),
                ConversationMessage.proyect_id == int(proyect_id),
            )
        )
        if since is not None:
            q = q.filter(ConversationMessage.created_at >= since)
        rows = (
            q.order_by(ConversationMessage.id.desc())
            .limit(max(1, limit))
            .all()
        )
        return list(reversed(rows))

    def purge_expired_messages(self, *, now: datetime | None = None) -> int:
        from datetime import timedelta, timezone

        moment = now or datetime.now(timezone.utc)
        deleted = 0
        for clave, days in PLAN_MEMORY_DAYS.items():
            cutoff = moment - timedelta(days=days)
            ids_q = (
                self.db.query(ConversationMessage.id)
                .join(
                    Conversation,
                    ConversationMessage.conversation_id == Conversation.id,
                )
                .join(User, User.id == Conversation.user_id)
                .filter(ConversationMessage.created_at < cutoff)
            )
            if clave == DEFAULT_PLAN:
                ids_q = ids_q.filter(
                    (User.plan_clave == clave) | (User.plan_clave.is_(None))
                )
            else:
                ids_q = ids_q.filter(User.plan_clave == clave)
            ids = [row[0] for row in ids_q.all()]
            if not ids:
                continue
            deleted += (
                self.db.query(ConversationMessage)
                .filter(ConversationMessage.id.in_(ids))
                .delete(synchronize_session=False)
            )
        self.db.commit()
        return deleted

    def count_for_user_proyect(self, user_id: int, proyect_id: int) -> int:
        return int(
            self.db.query(ConversationMessage.id)
            .join(
                Conversation,
                ConversationMessage.conversation_id == Conversation.id,
            )
            .filter(
                Conversation.user_id == int(user_id),
                ConversationMessage.proyect_id == int(proyect_id),
            )
            .count()
        )

    def delete_for_user_proyect(self, user_id: int, proyect_id: int) -> int:
        ids = [
            row[0]
            for row in (
                self.db.query(ConversationMessage.id)
                .join(
                    Conversation,
                    ConversationMessage.conversation_id == Conversation.id,
                )
                .filter(
                    Conversation.user_id == int(user_id),
                    ConversationMessage.proyect_id == int(proyect_id),
                )
                .all()
            )
        ]
        if not ids:
            return 0
        deleted = (
            self.db.query(ConversationMessage)
            .filter(ConversationMessage.id.in_(ids))
            .delete(synchronize_session=False)
        )
        self.db.commit()
        self.db.expire_all()
        return int(deleted or 0)

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        # Evitar que Conversation.messages quede stale en la misma request.
        self.db.expire_all()
        return message

    def update_status(self, conversation_id: str, status: str) -> None:
        conversation = self.db.get(Conversation, conversation_id)
        if conversation:
            conversation.status = status
            self.db.commit()
