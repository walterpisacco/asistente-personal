import logging
from typing import Any

from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class MockLLMProvider(LLMProvider):
    """Respuesta local para demos sin OpenAI/Ollama."""

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
        return (
            "¡Qué buena pregunta! Este Malbec Reserva se luce con un asado, "
            "unas pastas con salsa o un queso bien curado. "
            "Si querés, te cuento más sobre su crianza en barrica."
        )
