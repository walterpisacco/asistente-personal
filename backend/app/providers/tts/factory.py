from app.core.config import Settings
from app.providers.tts.base import TTSProvider
from app.providers.tts.deepgram import DeepgramTTSProvider
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider
from app.providers.tts.google import GoogleTTSProvider
from app.providers.tts.mock import MockTTSProvider


def create_tts_provider(settings: Settings) -> TTSProvider:
    provider = settings.tts_provider.lower()
    if provider == "deepgram":
        return DeepgramTTSProvider(
            api_key=settings.deepgram_api_key,
            default_voice=settings.deepgram_voice,
            base_url=settings.deepgram_url,
        )
    if provider == "elevenlabs":
        return ElevenLabsTTSProvider(
            api_key=settings.elevenlabs_api_key,
            speed=settings.elevenlabs_speed,
        )
    if provider == "google":
        return GoogleTTSProvider(
            credentials_path=settings.google_application_credentials,
            language=settings.google_tts_language,
            voice=settings.google_tts_voice,
            speaking_rate=settings.google_tts_speaking_rate,
            pitch=settings.google_tts_pitch,
        )
    if provider == "mock":
        return MockTTSProvider()
    raise ValueError(f"Unsupported TTS_PROVIDER: {settings.tts_provider}")
