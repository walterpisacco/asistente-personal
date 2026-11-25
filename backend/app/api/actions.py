from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.repositories.action_repository import ActionRepository
from app.schemas.action import ActionExampleOut
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("/examples", response_model=list[ActionExampleOut])
def list_action_examples(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[ActionExampleOut]:
    rows = ActionRepository(db).list_examples()
    return [ActionExampleOut.model_validate(row) for row in rows]
