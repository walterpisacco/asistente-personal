from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Query, Session

from app.models.user import User
from app.models.user_embeddings import UserEmbedding
from app.models.user_profile import UserProfile
from app.repositories.proyects_repository import ProyectsRepository


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _alive(self, query: Query) -> Query:
        return query.filter(User.deleted_at.is_(None))

    def get_by_id(self, user_id: int | str) -> Optional[User]:
        return self._alive(self.db.query(User).filter(User.id == user_id)).first()

    def get_profile_text(self, user_id: int | str) -> str:
        row = (
            self.db.query(UserProfile)
            .filter(UserProfile.user_id == user_id)
            .order_by(UserProfile.id.desc())
            .first()
        )
        if row is None:
            return ""
        return (row.profile or "").strip()

    def upsert_profile(self, user_id: int | str, profile: str) -> UserProfile:
        text = (profile or "").strip()
        row = (
            self.db.query(UserProfile)
            .filter(UserProfile.user_id == user_id)
            .order_by(UserProfile.id.desc())
            .first()
        )
        if row is None:
            row = UserProfile(user_id=int(user_id), profile=text)
            self.db.add(row)
        else:
            row.profile = text
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_by_alias(self, alias: str) -> Optional[User]:
        return self._alive(self.db.query(User).filter(User.alias == alias)).first()

    def get_by_username(self, username: str) -> Optional[User]:
        return self._alive(
            self.db.query(User).filter(User.username == username)
        ).first()

    def list_all(self) -> list[User]:
        return (
            self._alive(self.db.query(User))
            .order_by(User.username.asc())
            .all()
        )

    def create(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        ProyectsRepository(self.db).ensure_default_for_user(int(user.id))
        return user

    def save(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def delete(self, user: User) -> None:
        self.soft_delete(user)

    def soft_delete(self, user: User) -> User:
        if user.deleted_at is None:
            now = datetime.now(timezone.utc)
            user.deleted_at = now
            user.is_active = False
            suffix = f"#d{user.id}"
            user.username = _retire_identifier(user.username, suffix)
            user.alias = _retire_identifier(user.alias, suffix)
        return self.save(user)

    def count(self) -> int:
        return self.db.query(User).count()

    def list_with_voice_embeddings(self) -> list[tuple[User, UserEmbedding]]:
        rows = (
            self._alive(self.db.query(User, UserEmbedding))
            .join(UserEmbedding, UserEmbedding.id_user == User.id)
            .filter(User.is_active.is_(True))
            .all()
        )
        return list(rows)


def _retire_identifier(value: str, suffix: str) -> str:
    base = (value or "").split("#d")[0]
    room = max(1, 80 - len(suffix))
    return f"{base[:room]}{suffix}"
