from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class CharacterCreate(BaseModel):
    id: Optional[str] = None
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = Field(min_length=1)
    voice_provider: str = "deepgram"
    voice_id: str = Field(min_length=1, max_length=128)
    avatar_url: Optional[str] = None
    animation: dict[str, Any] = Field(default_factory=dict)
    free_plan: bool = False
    basic_plan: bool = False
    premium_plan: bool = False


class CharacterUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    voice_provider: Optional[str] = None
    voice_id: Optional[str] = None
    avatar_url: Optional[str] = None
    animation: Optional[dict[str, Any]] = None
    free_plan: Optional[bool] = None
    basic_plan: Optional[bool] = None
    premium_plan: Optional[bool] = None


class CharacterAdmin(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str = ""
    system_prompt: str
    voice_provider: str = "deepgram"
    voice_id: str
    avatar_url: Optional[str] = None
    animation: dict[str, Any] = Field(default_factory=dict)
    free_plan: bool = False
    basic_plan: bool = False
    premium_plan: bool = False
