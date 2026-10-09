"""STT streaming de Deepgram (Nova-3) mientras el mic captura la frase."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from urllib.parse import urlencode, urlparse, urlunparse

import numpy as np

logger = logging.getLogger(__name__)

_CLOSE = None
_ABORT = False


class DeepgramLiveSession:
    """Manda PCM16 al WebSocket y acumula el transcript final.

    `start` no espera el handshake: el audio entra en una cola y sale
    cuando el socket abre, así el comienzo de la frase no se pierde.
    """

    def __init__(self, provider, *, sample_rate: int = 16000) -> None:
        self._provider = provider
        self.sample_rate = sample_rate
        self.transcript = ""
        self.flush_ms = 0
        self.failed = False
        self.consumed = False
        self._finals: list[str] = []
        self._interim = ""
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._active = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None

    def start(self) -> None:
        self.abort()
        self.transcript = ""
        self.flush_ms = 0
        self.failed = False
        self.consumed = False
        self._finals = []
        self._interim = ""
        self._q = queue.Queue()
        self._loop = None
        self._ws = None
        self._active = True
        self._thread = threading.Thread(
            target=self._thread_main, name="dg-stt", daemon=True
        )
        self._thread.start()

    def push(self, pcm: np.ndarray) -> None:
        if not self._active or pcm.size == 0:
            return
        self._q.put(np.ascontiguousarray(pcm, dtype=np.int16).tobytes())

    def finish(self) -> str:
        """Cierra el stream y espera el transcript. El tiempo es el flush."""
        if not self._active:
            return self.transcript
        t0 = time.perf_counter()
        self._q.put(_CLOSE)
        if self._thread is not None:
            self._thread.join(timeout=4)
            if self._thread.is_alive():
                self._force_close()
                self._thread.join(timeout=1)
            if self._thread.is_alive():
                self.failed = True
                logger.warning("Deepgram live STT no cerró a tiempo")
        self.flush_ms = int((time.perf_counter() - t0) * 1000)
        self._active = False
        self.consumed = True
        self.transcript = " ".join([*self._finals, self._interim]).strip()
        return self.transcript

    def abort(self) -> None:
        if not self._active:
            return
        self._q.put(_ABORT)
        self._force_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._active = False
        self.transcript = ""
        self._finals = []
        self._interim = ""

    def _listen_url(self) -> str:
        parsed = urlparse(self._provider.base_url)
        scheme = "wss" if parsed.scheme in {"https", "wss", ""} else "ws"
        lang = self._provider.language or "es-419"
        query = urlencode(
            {
                "model": self._provider.model,
                "language": lang,
                "encoding": "linear16",
                "sample_rate": str(self.sample_rate),
                "channels": "1",
                "punctuate": "true",
                "interim_results": "true",
                "endpointing": "false",
            }
        )
        return urlunparse((scheme, parsed.netloc or "api.deepgram.com", "/v1/listen", "", query, ""))

    def _force_close(self) -> None:
        loop = self._loop
        ws = self._ws
        if loop is None or ws is None or not loop.is_running():
            return

        def _abort() -> None:
            transport = getattr(ws, "transport", None)
            if transport is not None:
                transport.abort()

        try:
            loop.call_soon_threadsafe(_abort)
        except RuntimeError:
            return

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001
            self.failed = True
            logger.warning("Deepgram live STT: %s", exc)

    async def _run(self) -> None:
        import websockets

        url = self._listen_url()
        headers = {"Authorization": f"Token {self._provider.api_key}"}
        async with websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=5,
            close_timeout=1,
            ping_interval=20,
            max_queue=64,
            proxy=None,
        ) as ws:
            self._loop = asyncio.get_running_loop()
            self._ws = ws
            logger.info(
                "Deepgram STT stream model=%s lang=%s",
                self._provider.model,
                self._provider.language,
            )
            send_task = asyncio.create_task(self._send(ws))
            recv_task = asyncio.create_task(self._recv(ws))
            try:
                await send_task
            finally:
                if not recv_task.done():
                    try:
                        await asyncio.wait_for(recv_task, timeout=5)
                    except Exception:  # noqa: BLE001
                        recv_task.cancel()
        self.transcript = " ".join([*self._finals, self._interim]).strip()

    async def _send(self, ws) -> None:
        while True:
            item = await asyncio.to_thread(self._q.get)
            if item is _ABORT:
                await ws.close()
                return
            if item is _CLOSE:
                await ws.send(json.dumps({"type": "CloseStream"}))
                return
            await ws.send(item)

    async def _recv(self, ws) -> None:
        import websockets

        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                self._ingest(msg)
        except websockets.ConnectionClosed:
            return

    def _ingest(self, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return
        if data.get("type") not in (None, "Results"):
            return
        channel = data.get("channel") or {}
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            return
        text = (alternatives[0].get("transcript") or "").strip()
        if not text:
            return
        if data.get("is_final"):
            self._finals.append(text)
            self._interim = ""
        else:
            self._interim = text
