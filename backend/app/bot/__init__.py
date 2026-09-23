"""Paquete del bot local TORI."""

__all__ = ["play_audio_bytes", "play_audio_file"]


def __getattr__(name: str):
    if name in {"play_audio_bytes", "play_audio_file"}:
        from app.bot import playback

        return getattr(playback, name)
    raise AttributeError(name)
