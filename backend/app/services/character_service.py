from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.character_repository import CharacterRepository
from app.schemas.character import CharacterPublic


class CharacterService:
    def __init__(self, db: Session) -> None:
        self.repo = CharacterRepository(db)

    def list_public(self, _user: User | None = None) -> list[CharacterPublic]:
        return [CharacterPublic.model_validate(row) for row in self.repo.list_all()]

    def get_public(self, character_id: str) -> CharacterPublic:
        character = self.repo.get_by_id(character_id)
        if not character:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Character not found: {character_id}",
            )
        return CharacterPublic.model_validate(character)
