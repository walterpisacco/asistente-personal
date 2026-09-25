#!/usr/bin/env python3
"""Trace en vivo del wake local (VAD + KWS). No llama al STT/LLM.

Uso:
  cd backend && .venv/bin/python scripts/trace_wake.py
  .venv/bin/python scripts/trace_wake.py --seconds 60
  .venv/bin/python scripts/trace_wake.py --threshold 0.15

Decí exactamente: «hola soy {username}» (ej. hola soy walter).
Ctrl+C para salir.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.bot.kws import activation_phrases, create_wake_spotter, encode_keyword_line
from app.bot.playback import MicStream
from app.bot.vad import UtteranceCapture
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.user import User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("tori.trace_wake")


def _usernames() -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(User.username)
            .filter(User.is_active.is_(True))
            .order_by(User.username)
            .all()
        )
    finally:
        db.close()
    names = [n.strip() for (n,) in rows if n and str(n).strip()]
    if not names:
        raise SystemExit("No hay usuarios activos en DB")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace VAD + wake word local")
    parser.add_argument("--seconds", type=float, default=0, help="0 = hasta Ctrl+C")
    parser.add_argument("--threshold", type=float, default=None, help="pisa BOT_KWS_THRESHOLD")
    parser.add_argument("--score", type=float, default=None, help="pisa BOT_KWS_SCORE")
    parser.add_argument("--vad-energy", type=float, default=None, help="pisa BOT_VAD_ENERGY")
    args = parser.parse_args()

    settings = get_settings()
    if args.threshold is not None:
        settings.bot_kws_threshold = args.threshold
    if args.score is not None:
        settings.bot_kws_score = args.score
    if args.vad_energy is not None:
        settings.bot_vad_energy = args.vad_energy

    usernames = _usernames()
    phrases = [p for u in usernames for p in activation_phrases(u)]
    model_dir = settings._resolve_path(settings.bot_kws_model_dir)

    logger.info("=== TRACE WAKE ===")
    logger.info("Usuarios activos: %s", usernames)
    logger.info("Frases esperadas: %s", phrases)
    logger.info(
        "VAD energy=%.0f | KWS threshold=%s score=%s trailing=%sms window=%sms",
        settings.bot_vad_energy,
        settings.bot_kws_threshold,
        settings.bot_kws_score,
        settings.bot_kws_trailing_ms,
        settings.bot_wake_window_ms,
    )
    for phrase in phrases:
        line = encode_keyword_line(
            phrase,
            bpe_path=model_dir / "bpe.model",
            tokens_path=model_dir / "tokens.txt",
            score=settings.bot_kws_score,
            threshold=settings.bot_kws_threshold,
        )
        pieces = line.split(" :")[0]
        n_pieces = len(pieces.split())
        logger.info("BPE %r → %d tokens: %s", phrase, n_pieces, pieces)
        if n_pieces > 8:
            logger.warning(
                "La frase %r tiene muchos tokens BPE (%d); el KWS suele fallar. "
                "Probá bajar --threshold 0.15 o 0.10",
                phrase,
                n_pieces,
            )

    spotter = create_wake_spotter(settings, usernames)
    capture = UtteranceCapture(
        sample_rate=settings.bot_sample_rate,
        silence_ms=settings.bot_silence_ms,
        min_speech_ms=settings.bot_min_speech_ms,
        max_utterance_ms=settings.bot_max_utterance_ms,
        vad_energy=settings.bot_vad_energy,
        preroll_ms=settings.bot_preroll_ms,
        end_call_ms=settings.bot_end_call_ms,
    )

    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    logger.info("Mic ON. Decí una de: %s", " | ".join(phrases))

    with MicStream(
        sample_rate=settings.bot_sample_rate,
        input_gain=settings.bot_mic_input_gain,
    ) as mic:
        from app.bot.playback import begin_listening

        begin_listening(mic, settle_ms=settings.bot_mic_settle_ms)
        while True:
            try:
                wav = capture.listen_for_wake(
                    mic,
                    spotter,
                    trailing_ms=settings.bot_kws_trailing_ms,
                    max_phrase_ms=settings.bot_wake_window_ms,
                    trace=True,
                    deadline_monotonic=deadline,
                )
            except TimeoutError:
                logger.info("Tiempo agotado sin wake")
                break
            user = spotter.matched_username()
            logger.info(
                ">>> ACTIVÓ keyword=%r usuario=%s wav=%s bytes",
                spotter.last_keyword,
                user or "?",
                len(wav),
            )
            if deadline is not None:
                break
            logger.info("Seguí probando o Ctrl+C…")
            spotter.reset()
            begin_listening(mic, settle_ms=settings.bot_mic_settle_ms)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Trace detenido")
