import logging
from typing import Any

from openai import AsyncOpenAI

from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1-mini",
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 1.0,
        max_tokens: int = 1000,
        timeout: int = 60,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI provider")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        payload = [{"role": "system", "content": system_prompt}, *messages]
        logger.info("OpenAI generate model=%s messages=%s", self.model, len(messages))
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=payload,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return (response.choices[0].message.content or "").strip()
