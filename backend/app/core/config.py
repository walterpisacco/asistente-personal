import json
from functools import lru_cache
from pathlib import Path
from typing import Any
import os
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Credenciales MySQL desde el .env de la raíz del repo (antes de importar el backend).
ENV_PATH = _BACKEND_ROOT / ".env"
if not ENV_PATH.is_file():
    raise SystemExit(f"No se encontró {ENV_PATH}")
load_dotenv(ENV_PATH, override=True)

host = os.getenv("CALL_METRICS_MYSQL_HOST", "localhost")
user = os.getenv("CALL_METRICS_MYSQL_USER", "root")
password = quote_plus(os.getenv("CALL_METRICS_MYSQL_PASSWORD", ""))
database = os.getenv("CALL_METRICS_MYSQL_DATABASE", "asistente")
port = os.getenv("CALL_METRICS_MYSQL_PORT", "3306")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = "development"
    database_url: str = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"

    bot_enabled: bool = True
    bot_greeting: str = "Hola"
    bot_opening_mode: str = "immediate"  # immediate | llm
    bot_system_prompt: str = ""
    bot_system_prompt_file: str = "prompts/bot_system_prompt.txt"
    bot_action_triggers_file: str = "config/bot_action_triggers.json"
    bot_action_triggers: str = ""
    bot_silence_ms: int = 900
    bot_end_call_ms: int = 10000
    bot_min_speech_ms: int = 400
    bot_max_utterance_ms: int = 12000
    bot_vad_energy: float = 400.0
    bot_preroll_ms: int = 700
    bot_wake_window_ms: int = 2500
    # Keyword spotting local (sherpa-onnx). El STT en la nube corre recién después.
    bot_kws_model_dir: str = "models/kws-es"
    bot_kws_repo: str = "jguerrisi/sherpa-onnx-kws-zipformer-es-3M-2026-08-08"
    bot_kws_score: float = 1.0
    bot_kws_threshold: float = 0.25
    bot_kws_trailing_ms: int = 1000
    bot_sample_rate: int = 16000
    bot_guest_user_id: str = "user_guest"
    # Tras TTS: pausa antes de reabrir mic + settle para descartar eco (etiquetas-ia).
    bot_post_tts_delay_ms: int = 500
    bot_mic_settle_ms: int = 450

    voice_id_threshold: float = 0.75

    llm_provider: str = "openai"
    stt_provider: str = "deepgram"
    tts_provider: str = "deepgram"

    stt_language: str = "es-AR"
    stt_model: str = "default"
    google_sample_rate_hertz: int = 16000

    google_application_credentials: str = Field(default="", alias="GOOGLE_CREDENTIAL")
    google_cloud_project: str = Field(default="", alias="GOOGLE_PROJECT_ID")
    google_tts_language: str = "es-US"
    google_tts_voice: str = "es-US-Neural2-A"
    google_tts_speaking_rate: float = 1.15
    google_tts_pitch: float = 0.0

    deepgram_api_key: str = ""
    deepgram_url: str = "https://api.deepgram.com"
    deepgram_model: str = "nova-3"
    deepgram_language: str = "es-419"
    deepgram_voice: str = "aura-2-celeste-es"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    openai_temperature: float = 1.0
    openai_max_tokens: int = 1000
    openai_timeout: int = 60

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout: int = 120

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "9rvdnhrYoXoUt4igKpBw"
    elevenlabs_speed: float = 1.10

    audio_dir: str = "static/audio"

    call_metrics_enabled: bool = True
    call_metrics_mysql_host: str = ""
    call_metrics_mysql_port: int = 3306
    call_metrics_mysql_user: str = ""
    call_metrics_mysql_password: str = ""
    call_metrics_mysql_database: str = ""

    def _resolve_path(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            return path
        return _BACKEND_ROOT / path

    @property
    def resolved_bot_system_prompt(self) -> str:
        inline = (self.bot_system_prompt or "").strip()
        if inline:
            return inline
        path = self._resolve_path(self.bot_system_prompt_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    @property
    def resolved_action_triggers(self) -> list[dict[str, Any]]:
        inline = (self.bot_action_triggers or "").strip()
        if inline:
            data = json.loads(inline)
            return data if isinstance(data, list) else []
        path = self._resolve_path(self.bot_action_triggers_file)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
