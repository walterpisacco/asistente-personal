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
    """Reproduce TTS en streaming vía ffplay.

    Acumula un preroll antes de abrir ffplay: con MP3 + pipe, arrancar en el
    primer chunk con nobuffer/low_delay corta el comienzo del audio.
    """

    def __init__(self, extension: str = "mp3", *, preroll_bytes: int | None = None) -> None:
        self.extension = (extension or "mp3").lstrip(".").lower()
        self.played = False
        self._proc: subprocess.Popen | None = None
        self._buffer = bytearray()
        # ~48 KiB de MP3 ≈ headers + frames estables para que ffplay no saltee el inicio.
        if preroll_bytes is not None:
            self._preroll = max(0, int(preroll_bytes))
        elif self.extension == "wav":
            self._preroll = 8_192
        else:
            self._preroll = 48_000
        self._started = False
        self._ffplay = shutil.which("ffplay")

    def start(self) -> None:
        """Compat: el arranque real ocurre en write() tras el preroll."""
        return

    def _spawn_ffplay(self) -> None:
        if self._proc is not None or not self._ffplay:
            return
        # Sin nobuffer/low_delay: esos flags tiraban los primeros frames del MP3.
        self._proc = subprocess.Popen(
            [
                self._ffplay,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-probesize",
                "65536",
                "-analyzeduration",
                "100000",
                "-i",
                "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _write_proc(self, data: bytes) -> bool:
        if self._proc is None or self._proc.stdin is None or not data:
            return False
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
            return True
        except BrokenPipeError:
            logger.warning("ffplay pipe rota; paso a fallback en memoria")
            self._proc = None
            return False

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._started and self._proc is not None:
            if self._write_proc(chunk):
                return
            # Pipe caída: acumular el resto y reproducir al final.
            self._buffer.extend(chunk)
            return

        self._buffer.extend(chunk)
        if self._started:
            return
        if len(self._buffer) < self._preroll and self._ffplay:
            return
        # Suficiente preroll (o sin ffplay → se toca todo al finish).
        if not self._ffplay:
            self._started = True
            return
        self._spawn_ffplay()
        self._started = True
        buffered = bytes(self._buffer)
        self._buffer.clear()
        if not self._write_proc(buffered):
            self._buffer.extend(buffered)

    def finish(self) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            if self._buffer:
                self._write_proc(bytes(self._buffer))
                self._buffer.clear()
            try:
                self._proc.stdin.close()
            except BrokenPipeError:
                pass
            self._proc.wait()
            self._proc = None
            self.played = True
            return
        if self._buffer:
            play_audio_bytes(bytes(self._buffer), extension=self.extension)
            self._buffer.clear()
            self.played = True


async def consume_tts(tts, text: str, voice_id: str, player: StreamPlayer | None):
    """Lee el TTS por chunks. Devuelve (audio, ttfb_ms, download_ms).

    Con streaming, el player arranca solo tras el preroll (no en el 1er chunk).
    `download_ms` no incluye lo que tarda en terminar de sonar.
    """
    parts: list[bytes] = []
    t0 = time.perf_counter()
    ttfb_ms = 0
    started = False
    async for chunk in tts.iter_audio(text, voice_id):
        if not chunk:
            continue
        if not started:
            ttfb_ms = int((time.perf_counter() - t0) * 1000)
            started = True
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


class PcmStreamPlayer:
    """Reproduce PCM16 mono en streaming (Realtime / sounddevice)."""

    INTERRUPT = b"\x00INTERRUPT"

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate = int(sample_rate)
        self.played = False
        self._sd = _sounddevice()
        self._stream = None
        self._buffer = bytearray()

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = self._sd.RawOutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=0,
        )
        self._stream.start()

    def write(self, chunk: bytes) -> None:
        if not chunk or chunk == self.INTERRUPT:
            return
        if self._stream is None:
            self.start()
        assert self._stream is not None
        # Completar frames de 2 bytes.
        self._buffer.extend(chunk)
        n = len(self._buffer) - (len(self._buffer) % 2)
        if n <= 0:
            return
        frame = bytes(self._buffer[:n])
        del self._buffer[:n]
        self._stream.write(frame)
        self.played = True

    def interrupt(self) -> None:
        self._buffer.clear()
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None

    def finish(self) -> None:
        if self._buffer and self._stream is not None:
            # Pad último byte suelto
            if len(self._buffer) % 2:
                self._buffer.append(0)
            try:
                self._stream.write(bytes(self._buffer))
            except Exception:
                pass
            self._buffer.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
