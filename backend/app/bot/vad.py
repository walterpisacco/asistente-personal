"""VAD por energía RMS + captura de utterance con preroll."""

from __future__ import annotations

import logging
from collections import deque
from typing import Protocol

import numpy as np

from app.bot.playback import MicStream, pcm16_to_wav_bytes, rms_energy

logger = logging.getLogger("tori.vad")


class KeywordSpotter(Protocol):
    def reset(self) -> None: ...

    def accept(self, pcm: np.ndarray) -> bool: ...


class UtteranceCapture:
    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        silence_ms: int = 900,
        min_speech_ms: int = 400,
        max_utterance_ms: int = 12000,
        vad_energy: float = 400.0,
        preroll_ms: int = 700,
        end_call_ms: int = 10000,
        block_ms: int = 30,
    ) -> None:
        self.sample_rate = sample_rate
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms
        self.vad_energy = vad_energy
        self.preroll_ms = preroll_ms
        self.end_call_ms = end_call_ms
        self.block_ms = block_ms

    def _preroll_blocks(self) -> int:
        return max(1, self.preroll_ms // self.block_ms)

    def capture_utterance(
        self,
        mic: MicStream,
        *,
        wait_for_speech: bool = True,
        idle_timeout_ms: int | None = None,
    ) -> tuple[bytes | None, str]:
        """
        Captura una frase. Retorna (wav_bytes|None, reason).
        reason: speech | silence_timeout | max_utterance | idle_end_call
        """
        preroll: deque[np.ndarray] = deque(maxlen=self._preroll_blocks())
        frames: list[np.ndarray] = []
        speaking = False
        speech_ms = 0
        silence_ms = 0
        utterance_ms = 0
        idle_ms = 0
        idle_limit = idle_timeout_ms

        while True:
            block = mic.read_block(timeout=1.0)
            if block is None:
                continue
            energy = rms_energy(block)
            is_voice = energy >= self.vad_energy

            if not speaking:
                preroll.append(block)
                if wait_for_speech and not is_voice:
                    idle_ms += self.block_ms
                    if idle_limit is not None and idle_ms >= idle_limit:
                        return None, "idle_end_call"
                    continue
                if is_voice or not wait_for_speech:
                    speaking = True
                    frames.extend(list(preroll))
                    preroll.clear()
                    speech_ms = self.block_ms if is_voice else 0
                    silence_ms = 0 if is_voice else self.block_ms
                    utterance_ms = len(frames) * self.block_ms
                    continue

            frames.append(block)
            utterance_ms += self.block_ms
            if is_voice:
                speech_ms += self.block_ms
                silence_ms = 0
            else:
                silence_ms += self.block_ms

            if utterance_ms >= self.max_utterance_ms:
                wav = pcm16_to_wav_bytes(np.concatenate(frames), self.sample_rate)
                return wav, "max_utterance"

            if (
                speech_ms >= self.min_speech_ms
                and silence_ms >= self.silence_ms
            ):
                wav = pcm16_to_wav_bytes(np.concatenate(frames), self.sample_rate)
                return wav, "speech"

            if speech_ms < self.min_speech_ms and silence_ms >= self.silence_ms * 2:
                # Ruido corto: reset
                speaking = False
                frames.clear()
                speech_ms = 0
                silence_ms = 0
                utterance_ms = 0
                idle_ms = 0

    def capture_wake_window(
        self,
        mic: MicStream,
        window_ms: int = 2500,
    ) -> bytes:
        """Captura una ventana fija para buscar wake word (siempre escuchando)."""
        n_blocks = max(1, window_ms // self.block_ms)
        frames: list[np.ndarray] = []
        # Espera a detectar voz o acumula ventana tras voz
        started = False
        silence_after = 0
        while len(frames) < n_blocks * 2:
            block = mic.read_block(timeout=1.0)
            if block is None:
                continue
            energy = rms_energy(block)
            if not started:
                if energy < self.vad_energy:
                    continue
                started = True
            frames.append(block)
            if energy < self.vad_energy:
                silence_after += self.block_ms
                if silence_after >= self.silence_ms and len(frames) * self.block_ms >= 600:
                    break
            else:
                silence_after = 0
            if len(frames) * self.block_ms >= window_ms:
                break
        if not frames:
            # silencio: devolver vacío corto
            return pcm16_to_wav_bytes(
                np.zeros(self.sample_rate // 10, dtype=np.int16), self.sample_rate
            )
        return pcm16_to_wav_bytes(np.concatenate(frames), self.sample_rate)

    def listen_for_wake(
        self,
        mic: MicStream,
        spotter: KeywordSpotter,
        *,
        trailing_ms: int = 1000,
        max_phrase_ms: int = 2500,
        trace: bool = False,
        deadline_monotonic: float | None = None,
    ) -> bytes:
        """Escucha hasta la wake word. La voz que no coincide se descarta.

        El spotter solo recibe audio después de que el VAD vio voz (más el
        preroll). Hay que seguir alimentándolo un rato de silencio: el modelo
        confirma la frase recién después de que la voz terminó.

        Si `deadline_monotonic` se cumple sin wake, lanza TimeoutError.
        """
        import time

        preroll: deque[np.ndarray] = deque(maxlen=self._preroll_blocks())
        frames: list[np.ndarray] = []
        speaking = False
        detected = False
        speech_ms = 0
        silence_ms = 0
        phrase_ms = 0
        peak_energy = 0.0
        blocks_fed = 0
        idle_trace_ms = 0

        while True:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("wake wait deadline")
            block = mic.read_block(timeout=1.0)
            if block is None:
                continue
            energy = rms_energy(block)
            is_voice = energy >= self.vad_energy

            if not speaking:
                preroll.append(block)
                if not is_voice:
                    if trace:
                        idle_trace_ms += self.block_ms
                        if idle_trace_ms >= 1000:
                            logger.info(
                                "trace idle: rms=%.0f (umbral VAD=%.0f)",
                                energy,
                                self.vad_energy,
                            )
                            idle_trace_ms = 0
                    continue
                speaking = True
                idle_trace_ms = 0
                frames = list(preroll)
                preroll.clear()
                spotter.reset()
                detected = False
                peak_energy = max(rms_energy(f) for f in frames) if frames else energy
                blocks_fed = 0
                for prev in frames:
                    blocks_fed += 1
                    if spotter.accept(prev):
                        detected = True
                        break
                speech_ms = self.block_ms
                silence_ms = 0
                phrase_ms = self.block_ms
                if trace or logger.isEnabledFor(logging.DEBUG):
                    logger.info(
                        "trace voz: rms=%.0f peak=%.0f preroll=%sms → kws",
                        energy,
                        peak_energy,
                        len(frames) * self.block_ms,
                    )
                if detected:
                    logger.info(
                        "Wake OK en preroll (peak_rms=%.0f phrase=%sms)",
                        peak_energy,
                        phrase_ms,
                    )
                    return pcm16_to_wav_bytes(np.concatenate(frames), self.sample_rate)
                continue

            frames.append(block)
            phrase_ms += self.block_ms
            peak_energy = max(peak_energy, energy)
            if is_voice:
                speech_ms += self.block_ms
                silence_ms = 0
            else:
                silence_ms += self.block_ms

            if not detected:
                blocks_fed += 1
                if spotter.accept(block):
                    detected = True

            timed_out = phrase_ms >= max_phrase_ms
            if detected:
                logger.info(
                    "Wake OK (peak_rms=%.0f speech=%sms phrase=%sms blocks=%s)",
                    peak_energy,
                    speech_ms,
                    phrase_ms,
                    blocks_fed,
                )
                return pcm16_to_wav_bytes(np.concatenate(frames), self.sample_rate)

            if not detected and (timed_out or silence_ms >= trailing_ms):
                # Al cortar por timeout a menudo falta el silencio final que el
                # KWS necesita (num_trailing_blanks). Empujamos ceros y reintentamos.
                if timed_out and not detected:
                    silence_block = np.zeros(
                        max(1, int(self.sample_rate * self.block_ms / 1000)),
                        dtype=np.int16,
                    )
                    for _ in range(max(1, trailing_ms // self.block_ms)):
                        if spotter.accept(silence_block):
                            detected = True
                            break
                    if detected:
                        logger.info(
                            "Wake OK tras flush silencio (peak_rms=%.0f phrase=%sms)",
                            peak_energy,
                            phrase_ms,
                        )
                        return pcm16_to_wav_bytes(
                            np.concatenate(frames), self.sample_rate
                        )

                reason = "timeout" if timed_out else "silence"
                if speech_ms >= self.min_speech_ms or trace:
                    logger.info(
                        "Voz sin wake word (%s): speech=%sms phrase=%sms "
                        "peak_rms=%.0f vad=%.0f blocks_kws=%s → descartada",
                        reason,
                        speech_ms,
                        phrase_ms,
                        peak_energy,
                        self.vad_energy,
                        blocks_fed,
                    )
                speaking = False
                frames = []
                detected = False
                speech_ms = 0
                silence_ms = 0
                phrase_ms = 0
                peak_energy = 0.0
                blocks_fed = 0
                spotter.reset()
