from abc import ABC, abstractmethod
import json
import re
from typing import Any

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJ_RE.search(text)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    raise ValueError("LLM JSON response is not an object")


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        raise NotImplementedError

    async def generate_json(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        *,
        temperature: float = 0,
        max_tokens: int = 150,
    ) -> dict[str, Any]:
        del temperature, max_tokens
        raw = await self.generate(messages, system_prompt)
        return parse_json_object(raw)
