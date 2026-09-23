"""Loop principal del bot TORI: idle → wake → identify → conversación → idle."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Permitir `python -m app.bot` desde backend/
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.bot.playback import MicStream, play_audio_bytes
from app.bot.vad import UtteranceCapture
from app.bot.wake import contains_wake_word
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.conversation_service import ConversationService
from app.services.voice_id_service import VoiceIdService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("tori.bot")


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

    logger.info(
        "TORI listo. Wake word=%r silence=%sms end_call=%sms stt=%s tts=%s llm=%s",
        settings.bot_wake_word,
        settings.bot_silence_ms,
        settings.bot_end_call_ms,
        settings.stt_provider,
        settings.tts_provider,
        settings.llm_provider,
    )

    with MicStream(sample_rate=settings.bot_sample_rate) as mic:
        while True:
            await _idle_and_session(mic, capture, settings)


async def _idle_and_session(mic, capture: UtteranceCapture, settings) -> None:
    logger.info("Escuchando wake word…")
    while True:
        wav = capture.capture_wake_window(mic, window_ms=2500)
        db = SessionLocal()
        try:
            service = ConversationService(db, settings)
            text = await service.transcribe_only(wav)
        finally:
            db.close()
        if not text:
            continue
        logger.info("Idle STT: %s", text)
        if contains_wake_word(text, settings.bot_wake_word):
            logger.info("Wake word detectada")
            break

    # Identificación: solo busca usuarios existentes (no crea).
    db = SessionLocal()
    try:
        voice_id = VoiceIdService(db, settings)
        user, score = voice_id.identify(wav)
        if user is None:
            logger.warning("Usuario no reconocido (score=%.3f). Volviendo a idle.", score)
            service = ConversationService(db, settings)
            character = service.characters.get_by_id(settings.bot_default_character_id)
            if character:
                msg = "No te reconocí. Decí TORI de nuevo cuando quieras."
                audio = await service.tts.synthesize(msg, character.voice_id)
                play_audio_bytes(
                    audio,
                    extension="wav" if settings.tts_provider == "mock" else "mp3",
                )
            return

        logger.info("Usuario=%s (%s) score=%.3f", user.id, user.full_name, score)

        service = ConversationService(db, settings)
        conversation, intro_audio, intro_text = await service.start_conversation(user)
        logger.info("Intro: %s", intro_text)
        play_audio_bytes(intro_audio, extension="wav" if settings.tts_provider == "mock" else "mp3")

        while True:
            utterance, reason = capture.capture_utterance(
                mic,
                wait_for_speech=True,
                idle_timeout_ms=settings.bot_end_call_ms,
            )
            if reason == "idle_end_call" or utterance is None:
                logger.info("Fin de conversación por silencio prolongado")
                service.end_conversation(conversation.id)
                bye = "Chau, acá estoy cuando me necesites."
                character = service.characters.get_by_id(conversation.character_id)
                if character:
                    bye_audio = await service.tts.synthesize(bye, character.voice_id)
                    play_audio_bytes(
                        bye_audio,
                        extension="wav" if settings.tts_provider == "mock" else "mp3",
                    )
                break

            result = await service.process_audio(conversation.id, utterance)
            user_text = result.get("user_text") or ""
            assistant_text = result.get("assistant_text") or ""
            logger.info("User: %s", user_text)
            logger.info("TORI: %s", assistant_text)
            audio_bytes = result.get("audio_bytes") or b""
            if audio_bytes:
                play_audio_bytes(
                    audio_bytes,
                    extension="wav" if settings.tts_provider == "mock" else "mp3",
                )
            action = result.get("action")
            if action:
                logger.info("Action: %s", action)
    finally:
        db.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot detenido")


if __name__ == "__main__":
    main()
