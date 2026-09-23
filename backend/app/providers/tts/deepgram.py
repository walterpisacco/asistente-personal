import logging
import re

import httpx

from app.providers.tts.base import TTSProvider

logger = logging.getLogger(__name__)

# Deepgram Aura voices look like "aura-2-celeste-es"
DEEPGRAM_VOICE_RE = re.compile(r"^aura-", re.I)


class DeepgramTTSProvider(TTSProvider):
    def __init__(
        self,
        api_key: str,
        default_voice: str = "aura-2-celeste-es",
        base_url: str = "https://api.deepgram.com",
    ) -> None:
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY is required for Deepgram TTS")
        self.api_key = api_key
        self.default_voice = default_voice
        self.base_url = base_url.rstrip("/")

    def _resolve_voice(self, voice_id: str) -> str:
        if voice_id and DEEPGRAM_VOICE_RE.match(voice_id):
            return voice_id
        return self.default_voice

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        model = self._resolve_voice(voice_id)
        url = f"{self.base_url}/v1/speak"
        params = {"model": model}
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": text}
        logger.info("Deepgram TTS model=%s chars=%s", model, len(text))
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, params=params, headers=headers, json=payload)
            response.raise_for_status()
            return response.content
