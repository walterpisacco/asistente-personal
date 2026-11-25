from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.user import (
    CharacterAssign,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
    UserSelfUpdate,
)
from app.services.auth_service import AuthService, get_current_user, to_user_public

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    return AuthService(db).login(payload)


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    return AuthService(db).register(payload)


@router.get("/me", response_model=UserPublic)
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPublic:
    return to_user_public(db, user)


@router.patch("/me", response_model=UserPublic)
def update_me(
    payload: UserSelfUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPublic:
    return AuthService(db).update_self(user, payload)


@router.patch("/me/character", response_model=UserPublic)
def assign_character(
    payload: CharacterAssign,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPublic:
    return AuthService(db).assign_character(user, payload.character_id)


@router.delete("/me", status_code=204)
def delete_me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    AuthService(db).delete_self(user)
    return Response(status_code=204)
