import asyncio
import logging
from pathlib import Path

from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)


class GoogleSTTProvider(STTProvider):
    def __init__(
        self,
        credentials_path: str = "",
        language: str = "es-AR",
        model: str = "default",
        sample_rate_hertz: int = 16000,
    ) -> None:
        self.credentials_path = credentials_path.strip().strip("'\"")
        self.language = language
        # "telephony" exige 8 kHz; "latest_short" no soporta es-AR.
        self.model = model if model not in {"", "none", "latest_short"} else "default"
        self.sample_rate_hertz = sample_rate_hertz

    def _client(self):
        from google.cloud import speech

        if not self.credentials_path:
            return speech.SpeechClient()

        path = Path(self.credentials_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise FileNotFoundError(
                f"Google credentials not found: {path}. "
                "Revisá GOOGLE_APPLICATION_CREDENTIALS en el .env."
            )
        return speech.SpeechClient.from_service_account_file(str(path))

    def _encoding_plan(self, audio: bytes) -> list[tuple[object, int | None]]:
        """Lista de (encoding, sample_rate_hertz|None)."""
        from google.cloud import speech

        Enc = speech.RecognitionConfig.AudioEncoding

        # AMR wideband: "#!AMR-WB\n" — SOLO 16 kHz. No probar AMR (8 kHz).
        if audio.startswith(b"#!AMR-WB"):
            return [(Enc.AMR_WB, 16000)]
        # AMR narrowband: "#!AMR\n" — SOLO 8 kHz.
        if audio.startswith(b"#!AMR"):
            return [(Enc.AMR, 8000)]
        if audio.startswith(b"RIFF"):
            return [(Enc.LINEAR16, self.sample_rate_hertz)]
        if audio[:3] == b"ID3" or audio[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            return [(Enc.MP3, None)]
        if len(audio) > 8 and audio[4:8] == b"ftyp":
            return [(Enc.ENCODING_UNSPECIFIED, None), (Enc.MP3, None)]
        return [
            (Enc.ENCODING_UNSPECIFIED, None),
            (Enc.LINEAR16, self.sample_rate_hertz),
            (Enc.MP3, None),
        ]

    def _attempts(self, language: str) -> list[tuple[str, str]]:
        langs = [language]
        for fallback in ("es-AR", "es-ES", "es-US"):
            if fallback not in langs:
                langs.append(fallback)

        models = [self.model]
        if "default" not in models:
            models.append("default")

        return [(lang, model) for lang in langs for model in models]

    async def transcribe(self, audio: bytes, language: str = "es-AR") -> str:
        lang = language or self.language

        def _sync_transcribe() -> str:
            from google.cloud import speech

            client = self._client()
            audio_content = speech.RecognitionAudio(content=audio)
            last_error: Exception | None = None
            plan = self._encoding_plan(audio)
            header = audio[:16]
            logger.info("Google STT audio header=%r plan=%s", header, [
                (enc.name if hasattr(enc, "name") else enc, rate) for enc, rate in plan
            ])

            for lang_code, model_name in self._attempts(lang):
                for encoding, sample_rate in plan:
                    config_kwargs: dict = {
                        "encoding": encoding,
                        "language_code": lang_code,
                        "enable_automatic_punctuation": True,
                        "model": model_name,
                        "audio_channel_count": 1,
                    }
                    if sample_rate is not None:
                        config_kwargs["sample_rate_hertz"] = sample_rate

                    try:
                        response = client.recognize(
                            config=speech.RecognitionConfig(**config_kwargs),
                            audio=audio_content,
                        )
                        transcripts = [
                            result.alternatives[0].transcript
                            for result in response.results
                            if result.alternatives
                        ]
                        text = " ".join(transcripts).strip()
                        if text:
                            logger.info(
                                "Google STT ok lang=%s model=%s encoding=%s rate=%s text=%s",
                                lang_code,
                                model_name,
                                encoding.name,
                                sample_rate,
                                text[:80],
                            )
                            return text
                        logger.warning(
                            "Google STT empty lang=%s model=%s encoding=%s",
                            lang_code,
                            model_name,
                            encoding.name,
                        )
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        logger.warning(
                            "Google STT fail lang=%s model=%s encoding=%s rate=%s: %s",
                            lang_code,
                            model_name,
                            encoding.name,
                            sample_rate,
                            exc,
                        )

            if last_error:
                raise last_error
            return ""

        logger.info(
            "Google STT transcribe bytes=%s lang=%s model=%s",
            len(audio),
            lang,
            self.model,
        )
        return await asyncio.to_thread(_sync_transcribe)
