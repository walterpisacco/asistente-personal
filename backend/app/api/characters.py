from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.character import CharacterPublic
from app.schemas.metrics import MetricEvent
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.character_service import CharacterService
from app.services.metrics_service import MetricsService

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("", response_model=list[CharacterPublic])
def list_characters(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CharacterPublic]:
    return CharacterService(db).list_public(user)


@router.get("/{character_id}", response_model=CharacterPublic)
def get_character(character_id: str, db: Session = Depends(get_db)) -> CharacterPublic:
    character = CharacterService(db).get_public(character_id)
    MetricsService(db).track(
        MetricEvent(
            event="character_activated",
            character_id=character.id,
        )
    )
    return character
