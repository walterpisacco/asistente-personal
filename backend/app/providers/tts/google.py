import asyncio
import logging
import re
from pathlib import Path

from app.providers.tts.base import TTSProvider

logger = logging.getLogger(__name__)

GOOGLE_VOICE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-")


class GoogleTTSProvider(TTSProvider):
    def __init__(
        self,
        credentials_path: str = "",
        language: str = "es-US",
        voice: str = "es-US-Neural2-A",
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
    ) -> None:
        self.credentials_path = credentials_path.strip().strip("'\"")
        self.language = language
        self.voice = voice
        self.speaking_rate = speaking_rate
        self.pitch = pitch

    def _client(self):
        from google.cloud import texttospeech

        if not self.credentials_path:
            return texttospeech.TextToSpeechClient()

        path = Path(self.credentials_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise FileNotFoundError(
                f"Google credentials not found: {path}. "
                "Revisá GOOGLE_CREDENTIAL en el .env."
            )
        return texttospeech.TextToSpeechClient.from_service_account_file(str(path))

    def _resolve_voice(self, voice_id: str) -> str:
        if voice_id and GOOGLE_VOICE_RE.match(voice_id):
            return voice_id
        return self.voice

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        voice_name = self._resolve_voice(voice_id)
        language_code = "-".join(voice_name.split("-")[:2]) or self.language

        def _sync_synthesize() -> bytes:
            from google.cloud import texttospeech

            client = self._client()
            response = client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=text),
                voice=texttospeech.VoiceSelectionParams(
                    language_code=language_code,
                    name=voice_name,
                ),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.MP3,
                    speaking_rate=self.speaking_rate,
                    pitch=self.pitch,
                ),
            )
            return response.audio_content

        logger.info(
            "Google TTS voice=%s lang=%s chars=%s", voice_name, language_code, len(text)
        )
        return await asyncio.to_thread(_sync_synthesize)
