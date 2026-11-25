import logging

from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)


class MockSTTProvider(STTProvider):
    """Dev fallback when Google credentials are not configured."""

    async def transcribe(self, audio: bytes, language: str = "es-AR") -> str:
        logger.warning("Mock STT used (audio bytes=%s language=%s)", len(audio), language)
        return "¿Con qué puedo acompañar este vino?"
