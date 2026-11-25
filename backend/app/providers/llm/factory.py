from app.core.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.ollama import OllamaLLMProvider
from app.providers.llm.openai import OpenAILLMProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "openai":
        return OpenAILLMProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    if provider == "ollama":
        return OllamaLLMProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
    if provider == "mock":
        return MockLLMProvider()
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
