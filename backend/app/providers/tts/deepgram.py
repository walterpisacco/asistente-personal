import logging
import re
from collections.abc import AsyncIterator

from app.providers.deepgram_http import deepgram_http
from app.providers.tts.base import TTSProvider

logger = logging.getLogger(__name__)

# Deepgram Aura voices look like "aura-2-celeste-es"
DEEPGRAM_VOICE_RE = re.compile(r"^aura-", re.I)


class DeepgramTTSProvider(TTSProvider):
    streams_audio = True

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
        parts: list[bytes] = []
        async for chunk in self.iter_audio(text, voice_id):
            parts.append(chunk)
        return b"".join(parts)

    async def iter_audio(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        model = self._resolve_voice(voice_id)
        url = f"{self.base_url}/v1/speak"
        params = {"model": model}
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": text}
        logger.info("Deepgram TTS model=%s chars=%s", model, len(text))
        client = deepgram_http()
        async with client.stream(
            "POST", url, params=params, headers=headers, json=payload
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                logger.warning("Deepgram TTS %s %s", response.status_code, body[:300])
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk
