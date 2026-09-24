from sqlalchemy.orm import Session

from app.models.character import Character


class CharacterRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, character_id: str) -> Character | None:
        return self.db.get(Character, character_id)

    def first(self) -> Character | None:
        return self.db.query(Character).order_by(Character.id).first()
