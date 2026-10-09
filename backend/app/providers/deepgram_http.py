"""Cliente HTTP reutilizado para Deepgram (keep-alive, sin handshake por turno)."""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def deepgram_http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
        )
    return _client
