"""Detección de palabra clave HOLA TORI en transcript."""

from __future__ import annotations

import re
import unicodedata


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def contains_wake_word(transcript: str, wake_word: str = "hola tori") -> bool:
    norm = _normalize(transcript)
    wake = _normalize(wake_word)
    if not wake:
        return False
    # Variantes comunes de STT
    variants = {wake, wake.replace("i", "y"), "tory", "tori", "tory"}
    tokens = set(norm.split())
    if any(v in tokens for v in variants):
        return True
    return any(v in norm for v in variants)
