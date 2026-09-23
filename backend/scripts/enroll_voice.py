#!/usr/bin/env python3
"""Enrolar embedding de voz con la misma frase corta del wake: «hola tori».

Usa la misma ventana de captura que el bot en idle (capture_wake_window),
para que el embedding coincida con el audio de identificación.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.bot.playback import MicStream
from app.bot.vad import UtteranceCapture
from app.bot.wake import contains_wake_word
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.user import User
from app.providers.stt.factory import create_stt_provider
from app.services.voice_id_service import VoiceIdService

# Misma ventana que el bot en idle (~frase corta "hola tori").
_WAKE_WINDOW_MS = 2500
_ENROLL_MAX_UTTERANCE_MS = 3000
_ENROLL_SILENCE_MS = 500
_ENROLL_MIN_SPEECH_MS = 300
_ENROLL_PREROLL_MS = 400


def _read_wav_file(path: Path) -> bytes:
    return path.read_bytes()


def _record_wake_phrase(settings, *, phrase: str, max_attempts: int = 3) -> bytes:
    """Graba una frase corta estilo wake; opcionalmente valida con STT."""
    capture = UtteranceCapture(
        sample_rate=settings.bot_sample_rate,
        silence_ms=_ENROLL_SILENCE_MS,
        min_speech_ms=_ENROLL_MIN_SPEECH_MS,
        max_utterance_ms=_ENROLL_MAX_UTTERANCE_MS,
        vad_energy=settings.bot_vad_energy,
        preroll_ms=_ENROLL_PREROLL_MS,
    )
    stt = create_stt_provider(settings)
    wake = settings.bot_wake_word

    print(f"Vas a enrolar con la frase corta del wake.")
    print(f'Decí exactamente: «{phrase}» (como cuando activás el bot).')
    print(f"Ventana ~{_WAKE_WINDOW_MS} ms (igual que idle).")

    last_wav: bytes | None = None
    last_text = ""

    with MicStream(sample_rate=settings.bot_sample_rate) as mic:
        for attempt in range(1, max_attempts + 1):
            print(f"\nIntento {attempt}/{max_attempts}: hablá ahora…")
            # Misma captura que el bot al detectar wake.
            wav = capture.capture_wake_window(mic, window_ms=_WAKE_WINDOW_MS)
            last_wav = wav
            try:
                text = asyncio.run(stt.transcribe(wav, language=settings.stt_language))
            except Exception as exc:  # noqa: BLE001
                print(f"STT falló ({exc}); se usa el audio igual.")
                text = ""
            last_text = (text or "").strip()
            print(f"STT: {last_text or '(vacío)'}")

            if last_text and contains_wake_word(last_text, wake):
                print("Frase OK (contiene wake word).")
                return wav

            print(
                f"No se detectó «{wake}» en el transcript. "
                f"Repetí solo: «{phrase}»."
            )

    assert last_wav is not None
    print(
        f"Aviso: se enrola con el último audio aunque STT no confirmó "
        f"la frase (último STT={last_text!r})."
    )
    return last_wav


def _list_users(db) -> None:
    users = db.query(User).order_by(User.id).all()
    if not users:
        print("No hay usuarios en la DB.")
        return
    print("Usuarios existentes:")
    for u in users:
        print(f"  {u.id}\t{u.username}\t{u.full_name or ''}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrolar voz con frase corta «hola tori» (mismo audio que el wake)"
    )
    parser.add_argument("--id", default=None, help="ID de usuario existente (ej. 1)")
    parser.add_argument(
        "--wav",
        type=Path,
        help="WAV ya grabado (si no, graba mic con frase corta)",
    )
    parser.add_argument(
        "--phrase",
        default="hola tori",
        help="Frase a pedir al usuario (default: hola tori)",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="Reintentos si STT no confirma la wake word",
    )
    parser.add_argument("--list", action="store_true", help="Listar usuarios y salir")
    args = parser.parse_args()

    settings = get_settings()
    db = SessionLocal()
    try:
        if args.list:
            _list_users(db)
            return

        user_id = args.id
        if not user_id:
            parser.error("Indicá --id de un usuario existente (o usá --list)")

        user = db.get(User, str(user_id))
        if user is None:
            _list_users(db)
            raise SystemExit(
                f"User not found: {user_id} (no se crea; usá un id de la lista)"
            )

        if args.wav:
            audio = _read_wav_file(args.wav)
        else:
            audio = _record_wake_phrase(
                settings, phrase=args.phrase, max_attempts=max(1, args.attempts)
            )

        print(f"Audio a enrolar: {len(audio)} bytes")
        service = VoiceIdService(db, settings)
        row = service.enroll(str(user_id), audio)
        db_name = db.get_bind().url.database
        print(
            f"Enrolado user={user_id} embedding_id={row.id} "
            f"dims={len(row.embedding_voice)} db={db_name}"
        )
        print("Tip: volvé a decir «hola tori» para activar el bot.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
