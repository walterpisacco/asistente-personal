import logging
from typing import Any

from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class MockLLMProvider(LLMProvider):
    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        last = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last = str(msg.get("content") or "")
                break
        logger.warning("Mock LLM used. last_user=%s", last[:80])
        return "Dale, te escucho. Decime en qué te puedo ayudar."
