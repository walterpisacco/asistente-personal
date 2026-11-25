from pydantic import BaseModel, ConfigDict, field_validator


class ActionExampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ejemplos: str
    orden: int | None = None

    @field_validator("ejemplos")
    @classmethod
    def strip_ejemplos(cls, value: str) -> str:
        return (value or "").strip()
