"""Reproducción de canciones del perfil YouTube del usuario (yt-dlp)."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from app.core.config import Settings
from app.models.user import User

logger = logging.getLogger(__name__)

# Proceso de reproducción en curso (ffplay/aplay) para poder detenerlo.
_playback_proc: Any = None

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
        search_bits = [profile.lstrip("@")]
        if artist:
            search_bits.append(artist)
        if genre:
            search_bits.append(genre)
        if target and target != "random":
            search_bits.append(target)
        search_query = "ytsearch1:" + " ".join(search_bits)

        try:
            info = await asyncio.to_thread(self._resolve_video, search_query)
        except Exception as exc:  # noqa: BLE001
            logger.exception("YouTube resolve failed: %s", exc)
            return {
                "ok": False,
                "spoken": "No pude buscar música ahora. Probá de nuevo en un rato.",
                "error": str(exc),
            }

        title = info.get("title") or "una canción"
        audio_path = info.get("audio_path")
        if audio_path:
            try:
                from app.bot.playback import play_audio_file
                import subprocess

                global _playback_proc
                self.stop_playback()
                # Reproducir en background para no bloquear el bot entero en canciones largas.
                for cmd in (
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                    ["aplay", audio_path],
                ):
                    try:
                        _playback_proc = subprocess.Popen(
                            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        break
                    except FileNotFoundError:
                        continue
                else:
                    await asyncio.to_thread(play_audio_file, audio_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("YouTube playback failed: %s", exc)

        label_bits = []
        if artist:
            label_bits.append(f"de {artist}")
        if genre:
            label_bits.append(f"de {genre}")
        suffix = (" " + " ".join(label_bits)) if label_bits else ""
        return {
            "ok": True,
            "title": title,
            "url": info.get("webpage_url"),
            "audio_path": audio_path,
            "spoken": f"Reproduzco {title}{suffix}.",
        }

    def _resolve_video(self, search_query: str) -> dict[str, Any]:
        import yt_dlp

        tmpdir = Path(tempfile.mkdtemp(prefix="hola_tori_yt_"))
        outtmpl = str(tmpdir / "%(id)s.%(ext)s")
        opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "default_search": "ytsearch",
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_query, download=True)
            if "entries" in info:
                info = info["entries"][0]
            path = Path(ydl.prepare_filename(info))
            if not path.exists():
                # yt-dlp may change extension after postprocessors
                matches = list(tmpdir.glob(f"{info.get('id', '')}.*"))
                path = matches[0] if matches else path
            return {
                "title": info.get("title"),
                "webpage_url": info.get("webpage_url"),
                "audio_path": str(path) if path.exists() else None,
            }

    def stop_playback(self) -> dict[str, Any]:
        """Detiene la reproducción de audio en curso, si hay."""
        global _playback_proc
        stopped = False
        if _playback_proc is not None:
            try:
                if _playback_proc.poll() is None:
                    _playback_proc.terminate()
                    try:
                        _playback_proc.wait(timeout=2)
                    except Exception:  # noqa: BLE001
                        _playback_proc.kill()
                    stopped = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("stop_playback failed: %s", exc)
            finally:
                _playback_proc = None
        return {
            "ok": True,
            "stopped": stopped,
            "spoken": "Listo, paro la música." if stopped else "No hay música reproduciéndose.",
        }
