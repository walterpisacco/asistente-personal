import logging
from typing import Any

import httpx

from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaLLMProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        model: str = "gemma3:4b",
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        # Soporta base_url con o sin /v1 (OpenAI-compat).
        if self.base_url.endswith("/v1"):
            chat_url = f"{self.base_url}/chat/completions"
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ],
                "stream": False,
            }
            async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
                response = await client.post(chat_url, json=payload)
                response.raise_for_status()
                data = response.json()
            return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()

        api_base = self.base_url
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
        }
        logger.info("Ollama generate model=%s", self.model)
        async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
            response = await client.post(f"{api_base}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        return (data.get("message", {}).get("content") or "").strip()
