import logging

import httpx

from app.providers.tts.base import TTSProvider

logger = logging.getLogger(__name__)


class ElevenLabsTTSProvider(TTSProvider):
    def __init__(self, api_key: str, speed: float = 1.0) -> None:
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for ElevenLabs provider")
        self.api_key = api_key
        self.speed = speed
        self.base_url = "https://api.elevenlabs.io/v1"

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        url = f"{self.base_url}/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.8},
        }
        logger.info("ElevenLabs TTS voice=%s chars=%s", voice_id, len(text))
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.content
