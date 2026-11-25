import logging
from typing import Any

import httpx

from app.providers.llm.base import LLMProvider, parse_json_object

logger = logging.getLogger(__name__)


class OllamaLLMProvider(LLMProvider):
    def __init__(self, base_url: str, model: str = "llama3.2") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
        }
        logger.info("Ollama generate model=%s", self.model)
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        return (data.get("message", {}).get("content") or "").strip()

    async def generate_json(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        *,
        temperature: float = 0,
        max_tokens: int = 150,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
        }
        logger.info("Ollama generate_json model=%s", self.model)
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        raw = (data.get("message", {}).get("content") or "").strip()
        return parse_json_object(raw)
