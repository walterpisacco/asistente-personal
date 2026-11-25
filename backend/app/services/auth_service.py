import logging
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, get_db
from app.core.plans import (
    DEFAULT_PLAN,
    character_available_for_plan,
    memory_days_for,
    normalize_plan_clave,
)
from app.models.user import User
from app.repositories.character_repository import CharacterRepository
from app.repositories.user_repository import UserRepository
from app.schemas.character import CharacterPublic
from app.schemas.user import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
    UserSelfUpdate,
    canonical_gender,
)

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


def to_user_public(db: Session, user: User) -> UserPublic:
    character = None
    if user.character_id:
        row = CharacterRepository(db).get_by_id(user.character_id)
        if row:
            character = CharacterPublic.model_validate(row)
    profile = UserRepository(db).get_profile_text(user.id) or None
    return UserPublic(
        # DB puede tener id INT (auto_increment); la API expone string.
        id=str(user.id),
        username=user.username,
        alias=user.alias,
        full_name=user.full_name,
        gender=canonical_gender(user.gender),
        role=user.role,
        character_id=user.character_id,
        character=character,
        profile=profile,
        is_active=user.is_active,
        plan_clave=normalize_plan_clave(getattr(user, "plan_clave", None)),
        plan_started_at=getattr(user, "plan_started_at", None),
        memory_days=memory_days_for(user),
        created_at=user.created_at,
    )


def bootstrap_admin_user(settings: Settings | None = None) -> None:
    """Crea el usuario admin inicial si la tabla users está vacía."""
    settings = settings or get_settings()
    db = SessionLocal()
    try:
        repo = UserRepository(db)
        if repo.count() > 0:
            return
        password = (settings.admin_bootstrap_password or "").strip()
        username = (settings.admin_bootstrap_username or "admin").strip()
        user = User(
            username=username,
            password_hash=hash_password(password),
            full_name="Administrator",
            role="admin",
            is_active=True,
            plan_clave=DEFAULT_PLAN,
            plan_started_at=datetime.now(timezone.utc),
        )
        repo.create(user)
        logger.warning(
            "Bootstrap admin created username=%s (change ADMIN_BOOTSTRAP_PASSWORD)",
            username,
        )
    finally:
        db.close()


class AuthService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.users = UserRepository(db)

    def login(self, payload: LoginRequest) -> TokenResponse:
        user = self.users.get_by_username(payload.username.strip())
        if not user or not user.is_active or user.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        token = create_access_token(
            str(user.id),
            self.settings,
            extra={"role": user.role, "username": user.username},
        )
        return TokenResponse(
            access_token=token,
            user=to_user_public(self.db, user),
        )

    def register(self, payload: RegisterRequest) -> TokenResponse:
        username = payload.username.strip()
        alias = payload.alias.strip()
        if self.users.get_by_username(username):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese email ya está en uso",
            )
        if self.users.get_by_alias(alias):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese alias ya está en uso",
            )
        characters = CharacterRepository(self.db).list_for_plan(DEFAULT_PLAN)
        if not characters:
            characters = CharacterRepository(self.db).list_all()
        character_id = characters[0].id if characters else None
        user = User(
            username=username,
            alias=alias,
            password_hash=hash_password(payload.password),
            role="operator",
            is_active=True,
            character_id=character_id,
            plan_clave=DEFAULT_PLAN,
            plan_started_at=datetime.now(timezone.utc),
        )
        try:
            created = self.users.create(user)
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese email o alias ya está en uso",
            ) from None
        token = create_access_token(
            str(created.id),
            self.settings,
            extra={"role": created.role, "username": created.username},
        )
        return TokenResponse(
            access_token=token,
            user=to_user_public(self.db, created),
        )

    def update_self(self, user: User, payload: UserSelfUpdate) -> UserPublic:
        alias = payload.alias.strip()
        taken = self.users.get_by_alias(alias)
        if taken is not None and taken.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese alias ya está en uso",
            )
        user.alias = alias
        user.full_name = (payload.full_name or "").strip() or None
        user.gender = payload.gender
        if payload.password:
            user.password_hash = hash_password(payload.password)
        saved = self.users.save(user)
        return to_user_public(self.db, saved)

    def assign_character(self, user: User, character_id: str) -> UserPublic:
        character_id = character_id.strip()
        character = CharacterRepository(self.db).get_by_id(character_id)
        if not character:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personaje no encontrado",
            )
        if not character_available_for_plan(character, user.plan_clave):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Este asistente no está disponible en tu plan ¿Quieres mejorar tu plan actual?",
            )
        user.character_id = character_id
        saved = self.users.save(user)
        return to_user_public(self.db, saved)

    def delete_self(self, user: User) -> None:
        if user.deleted_at is not None:
            return
        self.users.soft_delete(user)

    def get_user_from_token(self, token: str) -> User:
        try:
            data = decode_access_token(token, self.settings)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc
        user_id = data.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
        user = self.users.get_by_id(user_id)
        if not user or not user.is_active or user.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User inactive or not found",
            )
        return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthService(db, settings).get_user_from_token(credentials.credentials)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


def require_staff(user: User = Depends(get_current_user)) -> User:
    if user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff role required",
        )
    return user
