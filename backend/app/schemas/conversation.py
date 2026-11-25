from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationCreate(BaseModel):
    device_id: Optional[str] = None
    proyect_id: Optional[int] = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    role: str
    text: str
    audio_url: Optional[str] = None
    proyect_id: Optional[int] = None
    created_at: Optional[datetime] = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    conversation_id: str = Field(validation_alias="id")
    user_id: Optional[str] = None
    character_id: str
    status: str
    messages: list[MessageOut] = Field(default_factory=list)

    @field_validator("user_id", mode="before")
    @classmethod
    def coerce_user_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class AudioBase64In(BaseModel):
    audio_base64: str
    mime_type: Optional[str] = "audio/mp4"
    filename: Optional[str] = "audio.m4a"
    proyect_id: Optional[int] = None


class TextConversationIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    proyect_id: Optional[int] = None


class AudioConversationResponse(BaseModel):
    conversation_id: str
    user_text: str
    assistant_text: str
    audio_url: str
    stream_url: Optional[str] = None
    character_state: str = "talking"
    latency: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    items_title: Optional[str] = None
    proyect_id: Optional[int] = None
    reload_chat: bool = False
