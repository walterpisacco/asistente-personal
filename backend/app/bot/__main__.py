"""Loop principal del bot TORI: conversación por turnos (mic ↔ TTS exclusivos).

Patrón como etiquetas-ia mobile:
  listening → (mic ON) captura
  thinking  → (mic OFF) STT/LLM/TTS
  talking   → (mic OFF) reproduce
  listening → reopen mic + settle
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.bot.kws import create_wake_spotter
from app.bot.playback import MicStream, begin_listening, speak
from app.bot.vad import UtteranceCapture
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.conversation_service import ConversationService
from app.services.voice_id_service import VoiceIdService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("tori.bot")


def _tts_ext(settings) -> str:
    return "wav" if settings.tts_provider == "mock" else "mp3"


def _speak(settings, mic: MicStream, audio: bytes) -> None:
    speak(
        audio,
        mic=mic,
        extension=_tts_ext(settings),
        post_tts_delay_ms=settings.bot_post_tts_delay_ms,
    )


def _listen(settings, mic: MicStream) -> None:
    begin_listening(mic, settle_ms=settings.bot_mic_settle_ms)


async def run_bot() -> None:
    settings = get_settings()
    if not settings.bot_enabled:
        logger.error("BOT_ENABLED=false")
        return

    capture = UtteranceCapture(
        sample_rate=settings.bot_sample_rate,
        silence_ms=settings.bot_silence_ms,
        min_speech_ms=settings.bot_min_speech_ms,
        max_utterance_ms=settings.bot_max_utterance_ms,
        vad_energy=settings.bot_vad_energy,
        preroll_ms=settings.bot_preroll_ms,
        end_call_ms=settings.bot_end_call_ms,
    )

    spotter = create_wake_spotter(settings)
    logger.info(
        "TORI listo. Wake local=%r silence=%sms end_call=%sms post_tts=%sms settle=%sms",
        settings.bot_wake_word,
        settings.bot_silence_ms,
        settings.bot_end_call_ms,
        settings.bot_post_tts_delay_ms,
        settings.bot_mic_settle_ms,
    )

    with MicStream(sample_rate=settings.bot_sample_rate) as mic:
        while True:
            await _idle_and_session(mic, capture, settings, spotter)


async def _idle_and_session(mic: MicStream, capture: UtteranceCapture, settings, spotter) -> None:
    logger.info("Estado=listening (VAD + wake local, sin STT)…")
    _listen(settings, mic)
    wav = capture.listen_for_wake(
        mic,
        spotter,
        trailing_ms=settings.bot_kws_trailing_ms,
        max_phrase_ms=settings.bot_wake_window_ms,
    )
    mic.stop()
    logger.info("Wake word detectada (%s)", getattr(spotter, "last_keyword", "") or settings.bot_wake_word)

    db = SessionLocal()
    try:
        voice_id = VoiceIdService(db, settings)
        user, score = voice_id.identify(wav)
        if user is None:
            logger.warning("Usuario no reconocido (score=%.3f).", score)
            service = ConversationService(db, settings)
            character = service.characters.get_by_id(settings.bot_default_character_id)
            if character:
                msg = "No te reconocí. Decí TORI de nuevo cuando quieras."
                audio = await service.tts.synthesize(msg, character.voice_id)
                logger.info("Estado=talking")
                _speak(settings, mic, audio)
            return

        logger.info("Usuario=%s (%s) score=%.3f", user.id, user.full_name, score)
        service = ConversationService(db, settings)

        logger.info("Estado=thinking (intro)")
        conversation, intro_audio, intro_text = await service.start_conversation(user)
        logger.info("Intro: %s", intro_text)
        logger.info("Estado=talking")
        _speak(settings, mic, intro_audio)

        while True:
            logger.info("Estado=listening")
            _listen(settings, mic)
            utterance, reason = capture.capture_utterance(
                mic,
                wait_for_speech=True,
                idle_timeout_ms=settings.bot_end_call_ms,
            )
            mic.stop()

            if reason == "idle_end_call" or utterance is None:
                logger.info("Fin de conversación por silencio prolongado")
                service.end_conversation(conversation.id)
                bye = "Chau, acá estoy cuando me necesites."
                character = service.characters.get_by_id(conversation.character_id)
                if character:
                    bye_audio = await service.tts.synthesize(bye, character.voice_id)
                    logger.info("Estado=talking (bye)")
                    _speak(settings, mic, bye_audio)
                break

            logger.info("Estado=thinking")
            result = await service.process_audio(conversation.id, utterance)
            user_text = result.get("user_text") or ""
            assistant_text = result.get("assistant_text") or ""
            logger.info("User: %s", user_text)
            logger.info("TORI: %s", assistant_text)

            audio_bytes = result.get("audio_bytes") or b""
            if audio_bytes:
                logger.info("Estado=talking")
                _speak(settings, mic, audio_bytes)

            action = result.get("action")
            if action:
                logger.info("Action: %s", action)
                if action.get("end_session"):
                    logger.info("Acción finalizar → cerrando conversación")
                    service.end_conversation(conversation.id)
                    break
    finally:
        mic.stop()
        db.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot detenido")


if __name__ == "__main__":
    main()
