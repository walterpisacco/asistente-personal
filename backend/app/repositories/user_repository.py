from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_embeddings import UserEmbedding


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def list_with_voice_embeddings(self) -> list[tuple[User, UserEmbedding]]:
        rows = (
            self.db.query(User, UserEmbedding)
            .join(UserEmbedding, UserEmbedding.id_user == User.id)
            .filter(User.is_active.is_(True))
            .all()
        )
        return list(rows)
