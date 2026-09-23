"""Utilidades de audio: WAV PCM16, RMS, mic por turnos (como etiquetas-ia)."""

from __future__ import annotations

import io
import logging
import time
import wave
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


def _sounddevice():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "sounddevice/PortAudio no disponible. "
            "Instalá: sudo apt install libportaudio2 portaudio19-dev"
        ) from exc
    return sd


def pcm16_to_wav_bytes(pcm: np.ndarray, sample_rate: int) -> bytes:
    if pcm.dtype != np.int16:
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def rms_energy(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    if pcm.dtype == np.int16:
        x = pcm.astype(np.float64)
    else:
        x = (pcm * 32767.0).astype(np.float64)
    return float(np.sqrt(np.mean(x * x)))


def play_audio_file(path: str) -> None:
    """Reproduce un archivo de audio local (wav/mp3 vía soundfile o subprocess)."""
    try:
        import soundfile as sf

        sd = _sounddevice()
        data, rate = sf.read(path, dtype="float32")
        sd.play(data, rate)
        sd.wait()
        return
    except Exception:
        pass
    import subprocess

    for cmd in (
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["aplay", path],
    ):
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError(f"No se pudo reproducir audio: {path}")


def play_audio_bytes(content: bytes, extension: str = "mp3") -> None:
    import tempfile
    from pathlib import Path

    suffix = f".{extension.lstrip('.')}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        play_audio_file(path)
    finally:
        Path(path).unlink(missing_ok=True)


class MicStream:
    """Micrófono con start/stop explícitos para conversación por turnos.

    Patrón (etiquetas-ia): mic solo abierto en estado listening;
    cerrado mientras el bot habla (TTS).
    """

    def __init__(self, sample_rate: int = 16000, block_ms: int = 30) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.block_size = max(1, int(sample_rate * block_ms / 1000))
        self._q: deque[np.ndarray] = deque()
        self._stream = None
        self._sd = _sounddevice()

    @property
    def is_active(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        pcm = np.clip(mono, -1.0, 1.0)
        self._q.append((pcm * 32767).astype(np.int16))

    def clear(self) -> None:
        self._q.clear()

    def start(self) -> None:
        if self._stream is not None:
            return
        self.clear()
        self._stream = self._sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.block_size,
            callback=self._callback,
        )
        self._stream.start()
        logger.debug("Mic ON")

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
            self.clear()
            logger.debug("Mic OFF")

    def read_block(self, timeout: float = 1.0) -> np.ndarray | None:
        if self._stream is None:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._q:
                return self._q.popleft()
            time.sleep(0.005)
        return None

    def __enter__(self) -> "MicStream":
        return self

    def __exit__(self, *args) -> None:
        self.stop()


def begin_listening(mic: MicStream, settle_ms: int = 450) -> None:
    """Abre el mic y descarta el arranque (eco / estabilización)."""
    mic.start()
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)
        mic.clear()


def speak(
    content: bytes,
    *,
    mic: MicStream | None = None,
    extension: str = "mp3",
    post_tts_delay_ms: int = 500,
) -> None:
    """Reproduce TTS con micrófono cerrado. No reabre el mic (lo hace begin_listening)."""
    if mic is not None:
        mic.stop()

    play_audio_bytes(content, extension=extension)

    if post_tts_delay_ms > 0:
        time.sleep(post_tts_delay_ms / 1000.0)
