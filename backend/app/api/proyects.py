from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.plans import CHAT_HISTORY_MAX_MESSAGES, memory_cutoff
from app.models.user import User
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.proyects_repository import ProyectsRepository
from app.schemas.conversation import MessageOut
from app.schemas.proyect import ProyectOut
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/proyects", tags=["proyects"])


@router.get("", response_model=list[ProyectOut])
def list_my_proyects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProyectOut]:
    rows = ProyectsRepository(db).list_for_user(int(user.id))
    return [ProyectOut.model_validate(row) for row in rows]


@router.get("/{proyect_id}/messages", response_model=list[MessageOut])
def list_proyect_messages(
    proyect_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[MessageOut]:
    proyect = ProyectsRepository(db).get_active_for_user(int(user.id), int(proyect_id))
    if proyect is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    rows = ConversationRepository(db).list_messages_for_user_proyect(
        int(user.id),
        int(proyect.id),
        since=memory_cutoff(user),
        limit=CHAT_HISTORY_MAX_MESSAGES,
    )
    return [MessageOut.model_validate(row) for row in rows]
