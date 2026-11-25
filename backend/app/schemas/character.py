from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AvatarPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    avatar_url: str


class CharacterPublic(BaseModel):
    """Safe payload for mobile — no system_prompt."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str = ""
    avatar_url: Optional[str] = None
    voice_provider: str = "deepgram"
    voice_id: str
    animation: dict[str, Any] = Field(default_factory=dict)
    avatars: list[AvatarPublic] = Field(default_factory=list)
    free_plan: bool = False
    basic_plan: bool = False
    premium_plan: bool = False


class CharacterInternal(CharacterPublic):
    system_prompt: str
