import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.character import CharacterPublic


UserRole = Literal["admin", "operator"]
UserGender = Literal["chico", "chica", "undisclosed"]
UserPlan = Literal["free", "basic", "premium"]

_GENDER_ALIASES = {
    "chico": "chico",
    "male": "chico",
    "hombre": "chico",
    "chica": "chica",
    "female": "chica",
    "mujer": "chica",
    "undisclosed": "undisclosed",
    "no quiero decirlo": "undisclosed",
}


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def canonical_email_username(value: str) -> str:
    email = value.strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError("El usuario debe ser un email válido")
    return email


def canonical_gender(value: object) -> UserGender | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return _GENDER_ALIASES.get(value.strip().lower())


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    alias: str
    full_name: Optional[str] = None
    gender: Optional[UserGender] = None
    role: str
    character_id: Optional[str] = None
    character: Optional[CharacterPublic] = None
    profile: Optional[str] = None
    is_active: bool = True
    plan_clave: UserPlan = "free"
    plan_started_at: Optional[datetime] = None
    memory_days: int = 1
    created_at: Optional[datetime] = None


class UserCreate(BaseModel):
    username: str = Field(min_length=5, max_length=80)
    alias: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=6, max_length=128)
    full_name: Optional[str] = None
    role: UserRole = "operator"
    character_id: Optional[str] = None
    profile: Optional[str] = None
    is_active: bool = True
    plan_clave: UserPlan = "free"

    @field_validator("username")
    @classmethod
    def email_username(cls, value: str) -> str:
        return canonical_email_username(value)


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    alias: str = Field(min_length=3, max_length=80)
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    role: Optional[UserRole] = None
    character_id: Optional[str] = None
    profile: Optional[str] = None
    is_active: Optional[bool] = None
    plan_clave: Optional[UserPlan] = None


class UserSelfUpdate(BaseModel):
    alias: str = Field(min_length=3, max_length=80)
    full_name: Optional[str] = Field(default=None, max_length=160)
    gender: UserGender
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, value: object) -> object:
        mapped = canonical_gender(value)
        return mapped if mapped is not None else value


class CharacterAssign(BaseModel):
    character_id: str = Field(min_length=1, max_length=64)


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=5, max_length=80)
    alias: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("username")
    @classmethod
    def email_username(cls, value: str) -> str:
        return canonical_email_username(value)

    @field_validator("alias")
    @classmethod
    def strip_alias(cls, value: str) -> str:
        alias = value.strip()
        if len(alias) < 3:
            raise ValueError("El alias debe tener al menos 3 caracteres")
        return alias


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic
