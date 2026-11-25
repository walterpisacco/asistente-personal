from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class MetricEvent(BaseModel):
    event: str
    user_id: Optional[int] = None
    character_id: Optional[str] = None
    conversation_id: Optional[str] = None
    device_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("user_id", mode="before")
    @classmethod
    def coerce_user_id(cls, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        return int(value)


class MetricEventResponse(BaseModel):
    ok: bool = True
    id: int
