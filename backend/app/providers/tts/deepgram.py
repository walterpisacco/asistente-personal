import logging
import re
import time
from collections.abc import AsyncIterator

from app.providers.deepgram_http import deepgram_http
from app.providers.tts.base import TTSProvider

logger = logging.getLogger(__name__)

# Deepgram voices: Aura ("aura-2-celeste-es") y Flux ("flux-…")
DEEPGRAM_VOICE_RE = re.compile(r"^(?:aura-|flux-)", re.I)


class DeepgramTTSProvider(TTSProvider):
    streams_audio = True

    def __init__(
        self,
        api_key: str,
        default_voice: str = "aura-2-celeste-es",
        base_url: str = "https://api.deepgram.com",
        speed: float = 1.0,
    ) -> None:
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY is required for Deepgram TTS")
        self.api_key = api_key
        self.default_voice = default_voice
        self.base_url = base_url.rstrip("/")
        self.speed = speed

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
        speak_path = "/v2/speak" if model.lower().startswith("flux-") else "/v1/speak"
        url = f"{self.base_url}{speak_path}"
        params: dict[str, str | float] = {
            "model": model,
            "speed": self.speed,
        }
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": text}
        logger.info(
            "Deepgram TTS model=%s speed=%s chars=%s",
            model,
            self.speed,
            len(text),
        )
        t0 = time.perf_counter()
        first_byte_ms: int | None = None
        client = deepgram_http()
        async with client.stream(
            "POST", url, params=params, headers=headers, json=payload
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                logger.warning("Deepgram TTS %s %s", response.status_code, body[:300])
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if not chunk:
                    continue
                if first_byte_ms is None:
                    first_byte_ms = int((time.perf_counter() - t0) * 1000)
                yield chunk
        total_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "Deepgram TTS primer byte %sms (total %sms)",
            first_byte_ms if first_byte_ms is not None else "-",
            total_ms,
        )
