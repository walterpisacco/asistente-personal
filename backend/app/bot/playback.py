"""Utilidades de audio: WAV PCM16, RMS, mic por turnos (como etiquetas-ia)."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
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

    def __init__(
        self,
        sample_rate: int = 16000,
        block_ms: int = 30,
        *,
        input_gain: float = 0.35,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.block_size = max(1, int(sample_rate * block_ms / 1000))
        # PipeWire/ALSA a veces entrega float >|1| y con DC; el clip duro a ±1
        # satura el PCM y el KWS deja de reconocer.
        self.input_gain = max(0.05, float(input_gain))
        self._q: deque[np.ndarray] = deque()
        self._stream = None
        self._sd = _sounddevice()
        self._last_block_at = 0.0

    @property
    def is_active(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        mono = indata[:, 0] if indata.ndim > 1 else indata
        x = mono.astype(np.float64, copy=False)
        # Quitar DC del bloque (el device llega con offset ~-1.7 y picos >|1|).
        x = x - np.mean(x)
        pcm = np.clip(x * self.input_gain * 32767.0, -32768, 32767).astype(np.int16)
        self._last_block_at = time.monotonic()
        self._q.append(pcm)

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
        self._last_block_at = 0.0
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

    def reopen(self) -> None:
        """Vuelve a abrir el input. ffplay a veces deja el dispositivo mudo."""
        logger.warning("Mic sin audio, reabro el input")
        self.stop()
        self.start()

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
    if mic.is_active and (
        mic._last_block_at == 0.0 or time.monotonic() - mic._last_block_at > 1.0
    ):
        mic.reopen()
        mic.clear()


class StreamPlayer:
    """Reproduce TTS apenas llega audio. Si no hay ffplay, junta el archivo y lo toca al final."""

    def __init__(self, extension: str = "mp3") -> None:
        self.extension = extension
        self.played = False
        self._proc: subprocess.Popen | None = None
        self._fallback = bytearray()

    def start(self) -> None:
        if self._proc is not None or shutil.which("ffplay") is None:
            return
        self._proc = subprocess.Popen(
            [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-fflags",
                "nobuffer",
                "-flags",
                "low_delay",
                "-probesize",
                "32768",
                "-analyzeduration",
                "0",
                "-i",
                "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def write(self, chunk: bytes) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.write(chunk)
                self._proc.stdin.flush()
                return
            except BrokenPipeError:
                self._proc = None
        self._fallback.extend(chunk)

    def finish(self) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except BrokenPipeError:
                pass
            self._proc.wait()
            self._proc = None
            self.played = True
            return
        if self._fallback:
            play_audio_bytes(bytes(self._fallback), extension=self.extension)
            self._fallback.clear()
            self.played = True


async def consume_tts(tts, text: str, voice_id: str, player: StreamPlayer | None):
    """Lee el TTS por chunks. Devuelve (audio, ttfb_ms, download_ms).

    Con un proveedor que streamea, el player arranca en el primer chunk.
    `download_ms` no incluye lo que tarda en terminar de sonar.
    """
    parts: list[bytes] = []
    t0 = time.perf_counter()
    ttfb_ms = 0
    started = False
    stream = bool(getattr(tts, "streams_audio", False))
    async for chunk in tts.iter_audio(text, voice_id):
        if not chunk:
            continue
        if not started:
            ttfb_ms = int((time.perf_counter() - t0) * 1000)
            started = True
            if player is not None and stream:
                player.start()
        parts.append(chunk)
        if player is not None:
            player.write(chunk)
    download_ms = int((time.perf_counter() - t0) * 1000) if started else 0
    if player is not None:
        player.finish()
    return b"".join(parts), ttfb_ms, download_ms


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
