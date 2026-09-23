from abc import ABC, abstractmethod


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio: bytes, language: str = "es-AR") -> str:
        raise NotImplementedError
