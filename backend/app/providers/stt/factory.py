from app.core.config import Settings
from app.providers.stt.base import STTProvider
from app.providers.stt.deepgram import DeepgramSTTProvider
from app.providers.stt.google import GoogleSTTProvider
from app.providers.stt.mock import MockSTTProvider


def create_stt_provider(settings: Settings) -> STTProvider:
    provider = settings.stt_provider.lower()
    if provider == "deepgram":
        return DeepgramSTTProvider(
            api_key=settings.deepgram_api_key,
            model=settings.deepgram_model,
            language=settings.deepgram_language,
            base_url=settings.deepgram_url,
        )
    if provider == "google":
        return GoogleSTTProvider(
            credentials_path=settings.google_application_credentials,
            language=settings.stt_language,
            model=settings.stt_model,
            sample_rate_hertz=settings.google_sample_rate_hertz,
        )
    if provider == "mock":
        return MockSTTProvider()
    raise ValueError(f"Unsupported STT_PROVIDER: {settings.stt_provider}")
