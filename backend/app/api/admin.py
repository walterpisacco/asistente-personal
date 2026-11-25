import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.core.database import get_db
from app.core.plans import normalize_plan_clave
from app.models.character import Character
from app.models.user import User
from app.repositories.character_repository import CharacterRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.admin_character import CharacterAdmin, CharacterCreate, CharacterUpdate
from app.schemas.user import UserCreate, UserPublic, UserUpdate
from app.services.auth_service import require_admin, require_staff, to_user_public

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Characters ---


@router.get("/characters", response_model=list[CharacterAdmin])
def list_characters(
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
) -> list[CharacterAdmin]:
    rows = CharacterRepository(db).list_all()
    return [CharacterAdmin.model_validate(r) for r in rows]


@router.post("/characters", response_model=CharacterAdmin, status_code=201)
def create_character(
    payload: CharacterCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
) -> CharacterAdmin:
    repo = CharacterRepository(db)
    char_id = (payload.id or "").strip() or f"char_{uuid.uuid4().hex[:10]}"
    if repo.get_by_id(char_id):
        raise HTTPException(status_code=409, detail="Character id already exists")
    character = Character(
        id=char_id,
        name=payload.name.strip(),
        description=payload.description or "",
        system_prompt=payload.system_prompt,
        voice_provider=payload.voice_provider or "deepgram",
        voice_id=payload.voice_id,
        avatar_url=payload.avatar_url,
        animation=payload.animation or {},
        free_plan=payload.free_plan,
        basic_plan=payload.basic_plan,
        premium_plan=payload.premium_plan,
    )
    return CharacterAdmin.model_validate(repo.create(character))


@router.get("/characters/{character_id}", response_model=CharacterAdmin)
def get_character_admin(
    character_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
) -> CharacterAdmin:
    character = CharacterRepository(db).get_by_id(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    return CharacterAdmin.model_validate(character)


@router.put("/characters/{character_id}", response_model=CharacterAdmin)
def update_character(
    character_id: str,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
) -> CharacterAdmin:
    repo = CharacterRepository(db)
    character = repo.get_by_id(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(character, key, value)
    return CharacterAdmin.model_validate(repo.save(character))


@router.delete("/characters/{character_id}", status_code=204)
def delete_character(
    character_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    repo = CharacterRepository(db)
    character = repo.get_by_id(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    repo.delete(character)


# --- Users ---


@router.get("/users", response_model=list[UserPublic])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[UserPublic]:
    return [to_user_public(db, u) for u in UserRepository(db).list_all()]


@router.post("/users", response_model=UserPublic, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> UserPublic:
    repo = UserRepository(db)
    if repo.get_by_username(payload.username.strip()):
        raise HTTPException(status_code=409, detail="Username already exists")
    character_id = (payload.character_id or "").strip() or None
    if character_id and not CharacterRepository(db).get_by_id(character_id):
        raise HTTPException(status_code=400, detail="character_id not found")
    user = User(
        username=payload.username.strip(),
        alias=payload.alias.strip(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        character_id=character_id,
        is_active=payload.is_active,
        plan_clave=normalize_plan_clave(payload.plan_clave),
        plan_started_at=datetime.now(timezone.utc),
    )
    created = repo.create(user)
    if payload.profile is not None:
        repo.upsert_profile(created.id, payload.profile)
    return to_user_public(db, created)


@router.put("/users/{user_id}", response_model=UserPublic)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> UserPublic:
    repo = UserRepository(db)
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data.pop("password"))
    if "character_id" in data:
        character_id = (data["character_id"] or "").strip() or None
        if character_id and not CharacterRepository(db).get_by_id(character_id):
            raise HTTPException(status_code=400, detail="character_id not found")
        data["character_id"] = character_id
    profile_set = "profile" in data
    profile = data.pop("profile", None) if profile_set else None
    if "plan_clave" in data:
        next_plan = normalize_plan_clave(data["plan_clave"])
        if next_plan != normalize_plan_clave(user.plan_clave):
            user.plan_started_at = datetime.now(timezone.utc)
        data["plan_clave"] = next_plan
    for key, value in data.items():
        setattr(user, key, value)
    saved = repo.save(user)
    if profile_set:
        repo.upsert_profile(saved.id, profile or "")
    return to_user_public(db, saved)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> None:
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    repo = UserRepository(db)
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    repo.delete(user)


@router.post("/memory/purge")
def purge_expired_memory(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, int]:
    deleted = ConversationRepository(db).purge_expired_messages()
    return {"deleted": deleted}
