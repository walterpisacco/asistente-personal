import logging

import httpx

from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)


class DeepgramSTTProvider(STTProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        language: str = "es-419",
        base_url: str = "https://api.deepgram.com",
    ) -> None:
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY is required for Deepgram STT")
        self.api_key = api_key
        self.model = model
        self.language = language
        self.base_url = base_url.rstrip("/")

    def _content_type(self, audio: bytes) -> str:
        if audio.startswith(b"RIFF"):
            return "audio/wav"
        if audio[:3] == b"ID3" or audio[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            return "audio/mpeg"
        if len(audio) > 8 and audio[4:8] == b"ftyp":
            return "audio/mp4"
        return "audio/wav"

    async def transcribe(self, audio: bytes, language: str = "es-AR") -> str:
        lang = language or self.language
        # Mapear es-AR → es-419 si el caller usa locale Google.
        if lang.lower().startswith("es") and self.language:
            lang = self.language
        url = f"{self.base_url}/v1/listen"
        params = {
            "model": self.model,
            "language": lang,
            "punctuate": "true",
            "smart_format": "true",
        }
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": self._content_type(audio),
        }
        logger.info("Deepgram STT bytes=%s lang=%s model=%s", len(audio), lang, self.model)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, params=params, headers=headers, content=audio)
            response.raise_for_status()
            data = response.json()
        try:
            alt = data["results"]["channels"][0]["alternatives"][0]
            return (alt.get("transcript") or "").strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Deepgram STT empty response: %s", data)
            return ""
