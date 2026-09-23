from sqlalchemy.orm import Session, joinedload

from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage


class ConversationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_by_id(self, conversation_id: str) -> Conversation | None:
        return (
            self.db.query(Conversation)
            .options(joinedload(Conversation.messages))
            .filter(Conversation.id == conversation_id)
            .first()
        )

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def update_status(self, conversation_id: str, status: str) -> None:
        conversation = self.db.get(Conversation, conversation_id)
        if conversation:
            conversation.status = status
            self.db.commit()
