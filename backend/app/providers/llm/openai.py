import logging
from typing import Any

from openai import AsyncOpenAI

from app.providers.llm.base import LLMProvider, parse_json_object

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI provider")
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        payload = [{"role": "system", "content": system_prompt}, *messages]
        logger.info(
            "OpenAI generate model=%s history_messages=%s roles=%s",
            self.model,
            len(messages),
            [m.get("role") for m in messages],
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=payload,
            temperature=0.7,
        )
        return (response.choices[0].message.content or "").strip()

    async def generate_json(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        *,
        temperature: float = 0,
        max_tokens: int = 150,
    ) -> dict[str, Any]:
        payload = [{"role": "system", "content": system_prompt}, *messages]
        logger.info(
            "OpenAI generate_json model=%s history_messages=%s",
            self.model,
            len(messages),
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=payload,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        return parse_json_object(raw)
