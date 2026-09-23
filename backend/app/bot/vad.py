"""VAD por energía RMS + captura de utterance con preroll."""

from __future__ import annotations

from collections import deque

import numpy as np

from app.bot.playback import MicStream, pcm16_to_wav_bytes, rms_energy


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
