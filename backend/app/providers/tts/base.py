from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class TTSProvider(ABC):
    #: True si `iter_audio` entrega chunks antes de tener el archivo entero.
    streams_audio = False

    @abstractmethod
    async def synthesize(self, text: str, voice_id: str) -> bytes:
        raise NotImplementedError

    async def iter_audio(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        audio = await self.synthesize(text, voice_id)
        if audio:
            yield audio
