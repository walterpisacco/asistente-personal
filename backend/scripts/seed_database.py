#!/usr/bin/env python3
"""Seed Character TORI, guest user y usuario de ejemplo."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Character, User
from app.models.actions import Action

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

TORI_PROMPT = """Sos TORI, un asistente personal de voz.
Hablás en español rioplatense, de forma cálida y natural.
Respondé solo con texto apto para TTS: frases cortas, sin markdown ni emojis.
No inventes datos del usuario: usá el JSON de contexto CRM si está disponible.
Podés charlar de cualquier tema. Si piden música, ofrecé reproducir del perfil de YouTube.
"""


def upsert_character(db: Session, settings) -> None:
    char = db.get(Character, settings.bot_default_character_id)
    if char is None:
        char = Character(
            id=settings.bot_default_character_id,
            name="TORI",
            description="Asistente personal de voz",
            system_prompt=TORI_PROMPT,
            voice_provider=settings.tts_provider,
            voice_id=settings.deepgram_voice or settings.elevenlabs_voice_id,
            animation={},
        )
        db.add(char)
    else:
        char.system_prompt = TORI_PROMPT
        char.voice_provider = settings.tts_provider
        char.voice_id = settings.deepgram_voice or settings.elevenlabs_voice_id


def upsert_user(
    db: Session,
    *,
    user_id: str,
    username: str,
    full_name: str,
    gender: str | None = None,
    age: int | None = None,
    youtube_profile: str | None = None,
    role: str = "user",
) -> None:
    user = db.get(User, user_id)
    if user is None:
        db.add(
            User(
                id=user_id,
                username=username,
                password_hash=pwd.hash("changeme"),
                full_name=full_name,
                gender=gender,
                age=age,
                youtube_profile=youtube_profile,
                role=role,
                is_active=True,
            )
        )
    else:
        user.full_name = full_name
        user.gender = gender
        user.age = age
        user.youtube_profile = youtube_profile
        user.is_active = True


def upsert_actions(db: Session) -> None:
    seeds = [
        {
            "clave": "ver_video",
            "valor": "madonna",
            "metodo": "ver_video",
            "description": "Reproducir música/video del perfil YouTube filtrando por valor",
        },
        {
            "clave": "detener_video",
            "valor": "",
            "metodo": "detener_video",
            "description": "Detener la reproducción de música/video en curso",
        },
        {
            "clave": "finalizar",
            "valor": "conversacion",
            "metodo": "finalizar_llm",
            "description": "Finalizar la conversación y desconectar el turno con el LLM",
        },
    ]
    for item in seeds:
        row = db.query(Action).filter(Action.clave == item["clave"]).first()
        if row is None:
            db.add(Action(**item))
        else:
            row.description = item["description"]
            row.valor = item["valor"]
            row.metodo = item["metodo"]


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        upsert_character(db, settings)
        upsert_actions(db)
        db.commit()
        print("Seed OK: character TORI + actions")
    finally:
        db.close()


if __name__ == "__main__":
    main()
