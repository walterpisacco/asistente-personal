"""Búsqueda en el perfil de YouTube y reproducción en una ventana PyQt6."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Optional
from urllib.parse import quote_plus

from app.bot.video_player import open_video, return_to_idle
from app.core.config import Settings
from app.models.user import User

logger = logging.getLogger(__name__)


class _YtdlpQuiet:
    """Evita que yt-dlp imprima el 404 del canal cuando hay búsqueda de respaldo."""

    def debug(self, msg: str) -> None:
        return

    def info(self, msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        return

    def error(self, msg: str) -> None:
        logger.info("yt-dlp: %s", msg)

_ARTIST_RE = re.compile(
    r"(?:de|del|por)\s+([a-záéíóúñü0-9 .'-]{2,40})",
    re.I,
)
_GENRE_HINTS = (
    "pop",
    "cumbia",
    "folklore",
    "folclore",
    "trap",
    "reggaeton",
    "reggae",
    "blues",
    "salsa",
    "tango",
)


class YouTubeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _extract_filters(self, query_hint: str) -> tuple[str | None, str | None]:
        text = (query_hint or "").lower()
        artist = None
        m = _ARTIST_RE.search(text)
        if m:
            artist = m.group(1).strip(" .")
        genre = next((g for g in _GENRE_HINTS if g in text), None)
        return artist, genre

    async def play_from_profile(
        self,
        *,
        user: Optional[User],
        query_hint: str = "",
        target: str = "random",
    ) -> dict[str, Any]:
        profile = (user.youtube_profile if user else None) or ""
        if not profile:
            return {
                "ok": False,
                "spoken": "No tengo un perfil de YouTube asociado a tu cuenta.",
            }

        artist, genre = self._extract_filters(query_hint)
        term = (target or "").strip()
        if not term or term.lower() == "random":
            term = " ".join(bit for bit in (artist, genre) if bit)
        term = self._search_term(profile, term)
        queries = self._search_queries(profile, term)

        try:
            info = await asyncio.to_thread(self._resolve_video, queries)
        except Exception as exc:  # noqa: BLE001
            logger.exception("YouTube resolve failed: %s", exc)
            return {
                "ok": False,
                "spoken": "No pude buscar música ahora. Probá de nuevo en un rato.",
                "error": str(exc),
            }

        title = info.get("title") or "un video"
        video_id = info.get("id") or ""
        try:
            open_video(video_id, title)
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo abrir la ventana de video: %s", exc)
            return {
                "ok": False,
                "spoken": "Encontré el video, pero no pude abrir la ventana.",
                "error": str(exc),
                "title": title,
                "video_id": video_id,
                "url": info.get("webpage_url"),
            }

        label_bits = []
        if artist:
            label_bits.append(f"de {artist}")
        if genre:
            label_bits.append(f"de {genre}")
        suffix = (" " + " ".join(label_bits)) if label_bits else ""
        return {
            "ok": True,
            "title": title,
            "video_id": video_id,
            "url": info.get("webpage_url"),
            "spoken": f"Reproduzco {title}{suffix}.",
        }

    def _search_term(self, profile: str, term: str) -> str:
        """El perfil no es un tema. «cualquier artista» tampoco."""
        handle = (profile or "").strip().lstrip("@").lower()
        cleaned = (term or "").strip()
        generic = {
            "",
            "random",
            "cualquiera",
            "cualquier",
            "cualquier artista",
            "cualquier tema",
            "algo",
            "lo que sea",
            "musica",
            "música",
            handle,
        }
        if cleaned.lower() in generic:
            return ""
        return cleaned

    def _search_queries(self, profile: str, term: str) -> list[str]:
        """Canal del perfil primero; si no hay resultados, el tema y al final música."""
        handle = (profile or "").strip().lstrip("@")
        term = (term or "").strip()
        queries: list[str] = []
        if handle and " " not in handle:
            if term:
                queries.append(
                    f"https://www.youtube.com/@{quote_plus(handle)}/search?query={quote_plus(term)}"
                )
            else:
                queries.append(f"https://www.youtube.com/@{quote_plus(handle)}/videos")
        if term:
            queries.append("ytsearch5:" + term)
        queries.append("ytsearch5:música")
        seen: list[str] = []
        for query in queries:
            if query not in seen:
                seen.append(query)
        return seen

    def _resolve_video(self, queries: list[str]) -> dict[str, Any]:
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": 5,
            "logger": _YtdlpQuiet(),
        }
        last_error: Exception | None = None
        with yt_dlp.YoutubeDL(opts) as ydl:
            for query in queries:
                try:
                    info = ydl.extract_info(query, download=False)
                except Exception as exc:  # noqa: BLE001
                    logger.info("YouTube sin resultados para %s (%s)", query, exc)
                    last_error = exc
                    continue
                entries = self._video_entries(info)
                if not entries:
                    logger.info("YouTube sin resultados para %s", query)
                    continue
                chosen = random.choice(entries)
                video_id = str(chosen["id"])
                url = chosen.get("webpage_url") or chosen.get("url") or ""
                if not str(url).startswith("http"):
                    url = f"https://www.youtube.com/watch?v={video_id}"
                logger.info("YouTube eligió %s (%s) desde %s", chosen.get("title"), video_id, query)
                return {
                    "id": video_id,
                    "title": chosen.get("title"),
                    "webpage_url": url,
                }
        raise RuntimeError("YouTube no devolvió un video") from last_error

    @staticmethod
    def _video_entries(info: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not info:
            return []
        if info.get("entries") is not None:
            return [item for item in info["entries"] if item and item.get("id")]
        if info.get("id") and info.get("_type") != "playlist":
            return [info]
        return []

    def stop_playback(self) -> dict[str, Any]:
        """Cierra la ventana del video, si está abierta."""
        try:
            stopped = return_to_idle()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop_playback failed: %s", exc)
            stopped = False
        return {
            "ok": True,
            "stopped": stopped,
            "spoken": "Listo, paro el video." if stopped else "No hay un video abierto.",
        }
