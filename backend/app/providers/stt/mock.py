import logging

from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)


class MockSTTProvider(STTProvider):
    async def transcribe(self, audio: bytes, language: str = "es-AR") -> str:
        logger.warning("Mock STT used (audio bytes=%s language=%s)", len(audio), language)
        return "hola tori cómo estás"
