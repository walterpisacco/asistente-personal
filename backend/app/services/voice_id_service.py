"""Identificación de usuario por embedding de voz (Resemblyzer)."""

from __future__ import annotations

import io
import logging
import wave
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.user import User
from app.models.user_embeddings import UserEmbedding
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder

        _encoder = VoiceEncoder()
    return _encoder


def _wav_bytes_to_pcm16(audio: bytes, sample_rate: int = 16000) -> np.ndarray:
    if audio.startswith(b"RIFF"):
        with wave.open(io.BytesIO(audio), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            if width == 2:
                pcm = np.frombuffer(frames, dtype=np.int16)
            else:
                pcm = np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128
            if channels > 1:
                pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
            if rate != sample_rate:
                # resampling lineal simple
                duration = len(pcm) / rate
                target_len = int(duration * sample_rate)
                x_old = np.linspace(0, 1, num=len(pcm), endpoint=False)
                x_new = np.linspace(0, 1, num=target_len, endpoint=False)
                pcm = np.interp(x_new, x_old, pcm.astype(np.float32)).astype(np.int16)
            return pcm.astype(np.float32) / 32768.0
    # raw PCM16 mono assumed
    pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm


def embed_voice(audio: bytes, sample_rate: int = 16000) -> list[float]:
    from resemblyzer import preprocess_wav

    wav = _wav_bytes_to_pcm16(audio, sample_rate=sample_rate)
    wav = preprocess_wav(wav, source_sr=sample_rate)
    encoder = _get_encoder()
    emb = encoder.embed_utterance(wav)
    return emb.astype(float).tolist()


def cosine_similarity(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class VoiceIdService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.users = UserRepository(db)

    def identify(self, audio: bytes) -> tuple[Optional[User], float]:
        """Busca usuario por embedding de voz. No crea usuarios.

        Returns:
            (user, score) si supera el umbral.
            (guest, score) si no hay match y existe BOT_GUEST_USER_ID en DB.
            (None, score) si no hay match y no hay guest en DB.
        """
        try:
            probe = embed_voice(audio, sample_rate=self.settings.bot_sample_rate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Voice embed failed: %s", exc)
            return self._lookup_guest(), 0.0

        best_user: Optional[User] = None
        best_score = -1.0
        for user, emb_row in self.users.list_with_voice_embeddings():
            if user.id == self.settings.bot_guest_user_id:
                continue
            score = cosine_similarity(probe, emb_row.embedding_voice)
            logger.info("VoiceId candidate user=%s score=%.3f", user.id, score)
            if score > best_score:
                best_score = score
                best_user = user

        threshold = self.settings.voice_id_threshold
        if best_user is not None and best_score >= threshold:
            logger.info(
                "VoiceId match user=%s score=%.3f threshold=%.3f",
                best_user.id,
                best_score,
                threshold,
            )
            return best_user, best_score

        guest = self._lookup_guest()
        logger.info(
            "VoiceId no match best=%.3f threshold=%.3f → %s",
            best_score,
            threshold,
            f"guest={guest.id}" if guest else "sin usuario",
        )
        return guest, max(best_score, 0.0)

    def _lookup_guest(self) -> Optional[User]:
        """Solo lectura: no crea el guest."""
        return self.users.get_by_id(self.settings.bot_guest_user_id)

    def enroll(self, user_id: str, audio: bytes) -> UserEmbedding:
        user = self.users.get_by_id(user_id)
        if not user:
            raise ValueError(f"User not found: {user_id}")
        vector = embed_voice(audio, sample_rate=self.settings.bot_sample_rate)

        existing = (
            self.db.query(UserEmbedding)
            .filter(UserEmbedding.id_user == user_id)
            .first()
        )
        if existing:
            existing.embedding_voice = vector
            self.db.commit()
            self.db.refresh(existing)
            logger.info(
                "Updated voice embedding user=%s embedding_id=%s dims=%s db=%s",
                user_id,
                existing.id,
                len(existing.embedding_voice or []),
                self.db.get_bind().url.database,
            )
            return existing

        row = UserEmbedding(id_user=user_id, embedding_voice=vector, embedding_face=None)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        logger.info(
            "Created voice embedding user=%s embedding_id=%s dims=%s db=%s",
            user_id,
            row.id,
            len(row.embedding_voice or []),
            self.db.get_bind().url.database,
        )
        return row
