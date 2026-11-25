from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.core.plans import character_plan_flag
from app.models.character import Character


class CharacterRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, character_id: str) -> Optional[Character]:
        return (
            self.db.query(Character)
            .options(joinedload(Character.avatars))
            .filter(Character.id == character_id)
            .first()
        )

    def list_all(self) -> list[Character]:
        return (
            self.db.query(Character)
            .options(joinedload(Character.avatars))
            .order_by(Character.name.asc())
            .all()
        )

    def list_for_plan(self, plan_clave: str | None) -> list[Character]:
        column = getattr(Character, character_plan_flag(plan_clave))
        return (
            self.db.query(Character)
            .options(joinedload(Character.avatars))
            .filter(column.is_(True))
            .order_by(Character.name.asc())
            .all()
        )

    def create(self, character: Character) -> Character:
        self.db.add(character)
        self.db.commit()
        self.db.refresh(character)
        return character

    def save(self, character: Character) -> Character:
        self.db.add(character)
        self.db.commit()
        self.db.refresh(character)
        return character

    def delete(self, character: Character) -> None:
        self.db.delete(character)
        self.db.commit()
