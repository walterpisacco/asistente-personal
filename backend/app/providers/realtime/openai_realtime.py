"""Cliente WebSocket para OpenAI Realtime API (GA).

Modelo de conversación: gpt-realtime-2.1
Transcripción de entrada: configurable (p.ej. gpt-4o-mini-transcribe).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

DEFAULT_REALTIME_URL = "wss://api.openai.com/v1/realtime"


class OpenAIRealtimeConnection:
    """Sesión Realtime de bajo nivel: connect → session.update → eventos."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-realtime-2.1",
        base_url: str = DEFAULT_REALTIME_URL,
        safety_identifier: str = "",
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for Realtime")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.safety_identifier = (safety_identifier or "").strip()[:128]
        self._ws: ClientConnection | None = None
        self._recv_task: asyncio.Task | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.session_id: str | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        if self._ws is not None:
            return
        query = urlencode({"model": self.model})
        url = f"{self.base_url}?{query}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.safety_identifier:
            headers["OpenAI-Safety-Identifier"] = self.safety_identifier
        logger.info("Realtime connect model=%s", self.model)
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        )
        self._recv_task = asyncio.create_task(self._reader(), name="realtime-reader")

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Realtime JSON inválido: %s", raw[:200])
                    continue
                if not isinstance(event, dict):
                    continue
                et = event.get("type")
                if et == "session.created":
                    session = event.get("session") or {}
                    self.session_id = session.get("id")
                    logger.info("Realtime session.created id=%s", self.session_id)
                elif et == "error":
                    logger.error("Realtime error: %s", event)
                await self._queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Realtime reader terminó con error")
        finally:
            await self._queue.put(None)

    async def send(self, event: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Realtime no conectado")
        await self._ws.send(json.dumps(event, ensure_ascii=False))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    async def update_session(self, session: dict[str, Any]) -> None:
        payload = dict(session)
        payload.setdefault("type", "realtime")
        payload.setdefault("model", self.model)
        await self.send({"type": "session.update", "session": payload})

    async def append_audio(self, pcm16_bytes: bytes) -> None:
        if not pcm16_bytes:
            return
        await self.send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16_bytes).decode("ascii"),
            }
        )

    async def clear_input_audio(self) -> None:
        await self.send({"type": "input_audio_buffer.clear"})

    async def create_response(self, **extra: Any) -> None:
        event: dict[str, Any] = {"type": "response.create"}
        if extra:
            event["response"] = extra
        await self.send(event)

    async def cancel_response(self) -> None:
        await self.send({"type": "response.cancel"})

    async def create_function_output(self, call_id: str, output: str) -> None:
        await self.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    async def close(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Drain
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("Realtime cerrado")
