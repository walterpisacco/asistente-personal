"""Loop principal del bot TORI: conversación por turnos (mic ↔ TTS exclusivos).

Patrón:
  login → wake «hola» → conversación → idle

Modos (`CONVERSATION_MODE`):
  - realtime: OpenAI Realtime (gpt-realtime-2.1) speech-to-speech
  - pipeline: STT → LLM → TTS clásico
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.bot.kws import create_wake_spotter
from app.bot.login_gate import load_user_from_session, wait_for_login
from app.bot.proyect_context import get_selected_proyect_id, resolve_proyect_id_for_user
from app.bot.playback import (
    MicStream,
    PcmStreamPlayer,
    StreamPlayer,
    begin_listening,
    consume_tts,
    speak,
)
from app.bot.vad import UtteranceCapture
from app.bot.video_player import is_showing_video, return_to_idle, show_idle
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.conversation_service import ConversationService
from app.services.realtime_conversation import RealtimeVoiceSession

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


def _pause_after_tts(settings) -> None:
    if settings.bot_post_tts_delay_ms > 0:
        time.sleep(settings.bot_post_tts_delay_ms / 1000.0)


async def _speak_text(settings, mic: MicStream, tts, text: str, voice_id: str, label: str) -> None:
    player = StreamPlayer(_tts_ext(settings))
    audio, ttfb_ms, total_ms = await consume_tts(tts, text, voice_id, player)
    logger.info(
        "Tiempos STT=- LLM=- TTS=%sms primer byte (total %sms) (%s)",
        ttfb_ms,
        total_ms,
        label,
    )
    if audio and not player.played:
        _speak(settings, mic, audio)
    else:
        _pause_after_tts(settings)


def _listen(settings, mic: MicStream) -> None:
    begin_listening(mic, settle_ms=settings.bot_mic_settle_ms)


def _session_user(db) -> User | None:
    """Usuario de la sesión de login (relee DB por si cambió character/plan)."""
    cached = load_user_from_session()
    if cached is None:
        return None
    return UserRepository(db).get_by_id(cached.id)


def _is_realtime(settings) -> bool:
    return (settings.conversation_mode or "realtime").strip().lower() == "realtime"


async def run_bot() -> None:
    settings = get_settings()
    if not settings.bot_enabled:
        logger.error("BOT_ENABLED=false")
        return

    if _is_realtime(settings) and not (settings.openai_api_key or "").strip():
        logger.error("CONVERSATION_MODE=realtime requiere OPENAI_API_KEY")
        return

    user = wait_for_login(settings)
    logger.info(
        "Autenticado como %s (%s) id=%s",
        user.username,
        user.full_name or user.alias,
        user.id,
    )

    capture = UtteranceCapture(
        sample_rate=settings.bot_sample_rate,
        silence_ms=settings.bot_silence_ms,
        min_speech_ms=settings.bot_min_speech_ms,
        max_utterance_ms=settings.bot_max_utterance_ms,
        vad_energy=settings.bot_vad_energy,
        preroll_ms=settings.bot_preroll_ms,
        end_call_ms=settings.bot_end_call_ms,
    )

    spotter = create_wake_spotter(settings, session_wake=True)
    try:
        show_idle()
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo abrir la ventana de inicio: %s", exc)
    mode = "realtime" if _is_realtime(settings) else "pipeline"
    logger.info(
        "TORI listo. Usuario=%s mode=%s model=%s wake=sesión",
        user.username,
        mode,
        settings.openai_realtime_model if mode == "realtime" else settings.openai_model,
    )

    with MicStream(
        sample_rate=settings.bot_sample_rate,
        input_gain=settings.bot_mic_input_gain,
    ) as mic:
        while True:
            await _idle_and_session(mic, capture, settings, spotter)


async def _idle_and_session(mic: MicStream, capture: UtteranceCapture, settings, spotter) -> None:
    logger.info("Estado=listening (VAD + wake «hola», sin STT)…")
    _listen(settings, mic)
    capture.listen_for_wake(
        mic,
        spotter,
        trailing_ms=settings.bot_kws_trailing_ms,
        max_phrase_ms=settings.bot_wake_window_ms,
        trace=bool(getattr(settings, "bot_wake_trace", False)),
    )
    mic.stop()
    command = spotter.matched_command()
    wake = spotter.matched_session_wake() or bool(spotter.matched_username())
    logger.info(
        "Wake word detectada (%s) wake=%s cmd=%s",
        spotter.last_keyword,
        wake,
        command or "-",
    )

    if not wake:
        if command == "detener_video" and is_showing_video():
            return_to_idle()
        elif command:
            logger.info("Comando %s fuera de contexto, ignorado", command)
        else:
            logger.warning("Frase %r no activa la sesión.", spotter.last_keyword)
        return

    if _is_realtime(settings):
        await _realtime_session(mic, capture, settings, spotter)
    else:
        await _pipeline_session(mic, capture, settings, spotter)


async def _realtime_session(mic: MicStream, capture: UtteranceCapture, settings, spotter) -> None:
    db = SessionLocal()
    session: RealtimeVoiceSession | None = None
    player = PcmStreamPlayer(sample_rate=int(settings.openai_realtime_sample_rate or 24000))
    try:
        user = _session_user(db)
        if user is None:
            logger.error("No hay sesión de usuario; volvé a iniciar el bot para loguearte")
            return

        logger.info(
            "Realtime user=%s (%s) model=%s tx=%s",
            user.id,
            user.full_name or user.username,
            settings.openai_realtime_model,
            settings.openai_realtime_transcription_model,
        )
        session = RealtimeVoiceSession(db, settings)
        conversation = await session.start(user)
        logger.info("Conversación realtime %s", conversation.id)

        local_playing = False

        async def play_out() -> None:
            nonlocal local_playing
            async for chunk in session.audio_chunks():
                if chunk is None:
                    local_playing = False
                    break
                if chunk == PcmStreamPlayer.INTERRUPT:
                    player.interrupt()
                    local_playing = False
                    continue
                if chunk == b"":
                    # Cola de red vacía; dejá que el device termine de sonar un poco.
                    await asyncio.sleep(0.12)
                    local_playing = False
                    continue
                local_playing = True
                player.write(chunk)

        play_task = asyncio.create_task(play_out(), name="realtime-play")
        # Half-duplex: mic cerrado al inicio (saludo) hasta que deje de hablar.
        mic_open = False
        was_playing = False

        try:
            while not session.ended:
                # Cubre tanto el flag del modelo como el audio aún en el parlante.
                playing = session.is_playing or local_playing

                # Transición a "talking": cortar mic y limpiar buffer remoto (anti-eco).
                if playing and not was_playing:
                    if mic_open:
                        mic.stop()
                        mic_open = False
                    await session.clear_input()
                    logger.info("Estado=talking (mic OFF, anti-eco)")

                # Transición a "listening": esperar eco residual y reabrir mic.
                if (not playing) and was_playing:
                    delay_ms = max(
                        int(settings.bot_post_tts_delay_ms or 0),
                        int(settings.bot_mic_settle_ms or 0),
                        500,
                    )
                    if delay_ms > 0:
                        await asyncio.sleep(delay_ms / 1000.0)
                    await session.clear_input()
                    _listen(settings, mic)
                    mic_open = True
                    logger.info("Estado=listening (mic ON tras settle %sms)", delay_ms)

                was_playing = playing

                if is_showing_video():
                    if mic_open:
                        mic.stop()
                        mic_open = False
                    logger.info(
                        "Estado=listening (video: wake o «detener video», sin Realtime)…"
                    )
                    _listen(settings, mic)
                    mic_open = True
                    capture.listen_for_wake(
                        mic,
                        spotter,
                        trailing_ms=settings.bot_kws_trailing_ms,
                        max_phrase_ms=settings.bot_wake_window_ms,
                        trace=bool(getattr(settings, "bot_wake_trace", False)),
                    )
                    mic.stop()
                    mic_open = False
                    command = spotter.matched_command()
                    if command == "detener_video":
                        logger.info("KWS detener_video → paro reproducción")
                        return_to_idle()
                        was_playing = False
                        continue
                    if spotter.matched_session_wake() or spotter.matched_username():
                        logger.info("Wake durante video → retomo Realtime")
                        was_playing = False
                        continue
                    was_playing = False
                    continue

                # Solo mandar audio al modelo cuando el parlante está en silencio.
                if not playing:
                    if not mic_open:
                        _listen(settings, mic)
                        mic_open = True
                    block = await asyncio.to_thread(mic.read_block, 0.35)
                    if block is not None and block.size:
                        await session.send_mic_pcm(
                            block, src_rate=int(settings.bot_sample_rate)
                        )
                else:
                    await asyncio.sleep(0.02)

                action = session.take_action()
                if action:
                    logger.info("Action realtime: %s", action)
                    if action.get("end_session"):
                        logger.info("Acción finalizar → cierro Realtime")
                        session.set_disconnect_reason("finalizar")
                        break

                if (not playing) and session.idle_timed_out(settings.bot_end_call_ms):
                    logger.info("Fin Realtime por silencio prolongado")
                    session.set_disconnect_reason("silence_timeout")
                    break
        finally:
            mic.stop()
            session.end()
            await session.close()
            player.finish()
            play_task.cancel()
            try:
                await play_task
            except asyncio.CancelledError:
                pass
            if is_showing_video():
                return_to_idle()
            _pause_after_tts(settings)
    except Exception:
        logger.exception("Error en sesión Realtime")
        if session is not None:
            try:
                session.set_disconnect_reason("error")
                session.flush_metrics(disconnect_reason="error")
                await session.close()
            except Exception:
                pass
    finally:
        mic.stop()
        db.close()


async def _pipeline_session(mic: MicStream, capture: UtteranceCapture, settings, spotter) -> None:
    db = SessionLocal()
    service: ConversationService | None = None
    conversation = None
    try:
        user = _session_user(db)
        if user is None:
            logger.error("No hay sesión de usuario; volvé a iniciar el bot para loguearte")
            return

        logger.info("Usuario=%s (%s)", user.id, user.full_name or user.username)
        service = ConversationService(db, settings)

        logger.info("Estado=thinking (intro)")
        intro_player = StreamPlayer(_tts_ext(settings))
        conversation, intro_audio, intro_text = await service.start_conversation(
            user, player=intro_player
        )
        logger.info("Intro: %s", intro_text)
        if intro_audio and not intro_player.played:
            logger.info("Estado=talking")
            _speak(settings, mic, intro_audio)
        else:
            logger.info("Estado=talking")
            _pause_after_tts(settings)

        while True:
            if is_showing_video():
                logger.info(
                    "Estado=listening (video: wake o «detener video», sin STT)…"
                )
                _listen(settings, mic)
                capture.listen_for_wake(
                    mic,
                    spotter,
                    trailing_ms=settings.bot_kws_trailing_ms,
                    max_phrase_ms=settings.bot_wake_window_ms,
                    trace=bool(getattr(settings, "bot_wake_trace", False)),
                )
                mic.stop()
                command = spotter.matched_command()
                if command == "detener_video":
                    logger.info("KWS detener_video → paro reproducción")
                    return_to_idle()
                    bye = "Listo, paro el video."
                    character = service.characters.get_by_id(conversation.character_id)
                    if character:
                        logger.info("Estado=talking (stop video)")
                        await _speak_text(
                            settings, mic, service.tts, bye, character.voice_id, "detener video"
                        )
                    continue
                if spotter.matched_session_wake() or spotter.matched_username():
                    logger.info(
                        "Wake durante video (%s) → retomo conversación",
                        spotter.last_keyword,
                    )
                    continue
                logger.info(
                    "Keyword %r ignorada en modo video",
                    spotter.last_keyword,
                )
                continue

            logger.info("Estado=listening")
            _listen(settings, mic)
            live = None
            if int(settings.bot_sample_rate) == 16000 and hasattr(service.stt, "open_live"):
                live = service.stt.open_live(sample_rate=settings.bot_sample_rate)
            utterance, reason = capture.capture_utterance(
                mic,
                wait_for_speech=True,
                idle_timeout_ms=settings.bot_end_call_ms,
                live=live,
            )
            mic.stop()

            if reason == "no_speech":
                continue

            if reason == "idle_end_call" or utterance is None:
                logger.info("Fin de conversación por silencio prolongado")
                if is_showing_video():
                    return_to_idle()
                service.end_conversation(
                    conversation.id, disconnect_reason="silence_timeout"
                )
                bye = "Chau, acá estoy cuando me necesites."
                character = service.characters.get_by_id(conversation.character_id)
                if character:
                    logger.info("Estado=talking (bye)")
                    await _speak_text(
                        settings, mic, service.tts, bye, character.voice_id, "despedida"
                    )
                break

            known_text = None
            stt_ms = None
            if live is not None and live.consumed and not live.failed and live.transcript.strip():
                known_text = live.transcript.strip()
                stt_ms = live.flush_ms
            elif live is not None and live.failed:
                logger.warning("STT streaming no disponible, transcribo el audio completo")

            logger.info("Estado=thinking")
            player = StreamPlayer(_tts_ext(settings))
            focus_proyect = resolve_proyect_id_for_user(db, user)
            logger.info("proyect_id=%s (UI=%s)", focus_proyect, get_selected_proyect_id())
            result = await service.process_audio_local(
                conversation.id,
                utterance,
                user_text=known_text,
                stt_ms=stt_ms,
                player=player,
                proyect_id=focus_proyect,
            )
            audio_bytes = result.get("audio_bytes") or b""
            if audio_bytes and not player.played:
                logger.info("Estado=talking")
                _speak(settings, mic, audio_bytes)
            elif player.played:
                logger.info("Estado=talking")
                _pause_after_tts(settings)

            action = result.get("action")
            if action:
                logger.info("Action: %s", action)
                if action.get("end_session"):
                    logger.info("Acción finalizar → cerrando conversación")
                    if is_showing_video():
                        return_to_idle()
                    service.end_conversation(
                        conversation.id, disconnect_reason="finalizar"
                    )
                    break
    except Exception:
        logger.exception("Error en sesión pipeline")
        if service is not None and conversation is not None:
            try:
                service.end_conversation(
                    conversation.id, disconnect_reason="error"
                )
            except Exception:
                logger.exception("No se pudo flushear call_sessions tras error")
        raise
    finally:
        # Si cortaron con Ctrl+C / salida sin silencio ni finalizar, igual persistir.
        if service is not None and conversation is not None:
            try:
                if conversation.id in getattr(service, "_call_metrics", {}):
                    service.end_conversation(
                        conversation.id, disconnect_reason="aborted"
                    )
            except Exception:
                logger.exception("No se pudo flushear call_sessions en finally")
        mic.stop()
        db.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot detenido")


if __name__ == "__main__":
    main()
