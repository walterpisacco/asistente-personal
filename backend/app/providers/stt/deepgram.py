import asyncio
import logging

import httpx

from app.providers.deepgram_http import deepgram_http
from app.providers.stt.base import STTProvider
from app.providers.stt.deepgram_live import DeepgramLiveSession

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

    def _content_types(self, audio: bytes) -> list[str]:
        if audio.startswith(b"RIFF"):
            return ["audio/wav"]
        if audio[:3] == b"ID3" or audio[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            return ["audio/mpeg"]
        if audio.startswith(b"#!AMR"):
            return ["audio/amr"]
        if len(audio) > 8 and audio[4:8] == b"ftyp":
            # Android m4a: Deepgram a veces falla con un solo Content-Type.
            return ["audio/mp4", "audio/m4a", "audio/aac", "application/octet-stream"]
        return ["audio/mp4", "audio/wav", "application/octet-stream"]

    def _languages(self, language: str) -> list[str]:
        requested = (language or "").strip()
        if requested.lower().startswith("es") and self.language:
            requested = self.language
        langs: list[str] = []
        for item in (requested, self.language, "es"):
            key = (item or "").strip()
            if key and key not in langs:
                langs.append(key)
        return langs

    async def _listen(self, audio: bytes, lang: str, content_type: str) -> str:
        url = f"{self.base_url}/v1/listen"
        params = {
            "model": self.model,
            "language": lang,
            "punctuate": "true",
            "smart_format": "true",
        }
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": content_type,
        }
        response = await deepgram_http().post(
            url, params=params, headers=headers, content=audio
        )
        response.raise_for_status()
        data = response.json()
        try:
            alt = data["results"]["channels"][0]["alternatives"][0]
            return (alt.get("transcript") or "").strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Deepgram STT empty response: %s", data)
            return ""

    async def transcribe(self, audio: bytes, language: str = "es-AR") -> str:
        langs = self._languages(language)
        types = self._content_types(audio)
        logger.info(
            "Deepgram STT bytes=%s lang=%s model=%s types=%s",
            len(audio),
            langs,
            self.model,
            types,
        )
        last_error: Exception | None = None
        attempts: list[tuple[str, str]] = [(types[0], langs[0])]
        for content_type in types[1:]:
            attempts.append((content_type, langs[0]))
        for lang in langs[1:]:
            attempts.append((types[0], lang))

        for content_type, lang in attempts:
            try:
                text = await self._listen(audio, lang, content_type)
                if text:
                    logger.info(
                        "Deepgram STT ok lang=%s type=%s text=%s",
                        lang,
                        content_type,
                        text[:80],
                    )
                    return text
                logger.warning(
                    "Deepgram STT empty lang=%s type=%s", lang, content_type
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                logger.warning(
                    "Deepgram STT HTTP %s lang=%s type=%s: %s",
                    status_code,
                    lang,
                    content_type,
                    exc.response.text[:200],
                )
                if status_code in {429, 500, 502, 503, 504}:
                    await asyncio.sleep(0.45)
                    try:
                        text = await self._listen(audio, lang, content_type)
                        if text:
                            return text
                    except Exception as retry_exc:  # noqa: BLE001
                        last_error = retry_exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Deepgram STT fail lang=%s type=%s: %s",
                    lang,
                    content_type,
                    exc,
                )
        if last_error and not isinstance(last_error, httpx.HTTPStatusError):
            raise last_error
        return ""

    def open_live(self, *, sample_rate: int = 16000) -> DeepgramLiveSession:
        return DeepgramLiveSession(self, sample_rate=sample_rate)
