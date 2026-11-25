"""Audio de TTS que el cliente puede reproducir mientras sigue llegando."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator

_streams: dict[str, "TtsByteStream"] = {}


class TtsByteStream:
    def __init__(self, token: str) -> None:
        self.token = token
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def push(self, chunk: bytes) -> None:
        if chunk:
            await self._queue.put(chunk)

    async def finish(self) -> None:
        await self._queue.put(None)

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await asyncio.wait_for(self._queue.get(), timeout=120)
                if item is None:
                    return
                yield item
        finally:
            _streams.pop(self.token, None)


def open_tts_stream() -> TtsByteStream:
    token = secrets.token_urlsafe(18)
    stream = TtsByteStream(token)
    _streams[token] = stream
    return stream


def get_tts_stream(token: str) -> TtsByteStream | None:
    return _streams.get(token)
