import logging
import struct
import math

from app.providers.tts.base import TTSProvider

logger = logging.getLogger(__name__)


class MockTTSProvider(TTSProvider):
    """Generates a short silent-ish WAV beep for local demos without ElevenLabs."""

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        logger.warning("Mock TTS used voice=%s chars=%s", voice_id, len(text))
        sample_rate = 16000
        duration_s = min(2.0 + len(text) * 0.02, 6.0)
        n_samples = int(sample_rate * duration_s)
        freq = 440.0
        samples = []
        for i in range(n_samples):
            # Soft tone envelope
            t = i / sample_rate
            # Amp alto para demos en parlante de celular; tono 440 Hz audible.
            amp = 0.55 * math.sin(2 * math.pi * freq * t) * max(0.15, 1 - t / duration_s)
            samples.append(int(amp * 32767))
        data = b"".join(struct.pack("<h", s) for s in samples)
        # WAV header
        byte_rate = sample_rate * 2
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + len(data),
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            sample_rate,
            byte_rate,
            2,
            16,
            b"data",
            len(data),
        )
        return header + data
