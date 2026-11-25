import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.geoip import lookup_country_code
from app.core.rate_limit import rate_limiter
from app.core.security import (
    is_dev_environment,
    is_private_or_loopback,
    secure_compare,
)
from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


def _ws_client_ip(websocket: WebSocket) -> str:
    if settings.trust_proxy_headers:
        forwarded = websocket.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = websocket.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    if websocket.client and websocket.client.host:
        return websocket.client.host
    return "0.0.0.0"


def _authorize_ws(websocket: WebSocket) -> str | None:
    """Devuelve mensaje de error o None si OK."""
    client_ip = _ws_client_ip(websocket)

    if settings.geo_restrict_enabled and not (
        is_private_or_loopback(client_ip)
        and (settings.geo_allow_private or is_dev_environment(settings.app_env))
    ):
        if not is_private_or_loopback(client_ip):
            country = websocket.headers.get("cf-ipcountry") or lookup_country_code(
                client_ip
            )
            allowed = {
                c.strip().upper()
                for c in settings.geo_restrict_countries.split(",")
                if c.strip()
            } or {"AR"}
            if not country or country.upper() not in allowed:
                return "Access restricted to allowed regions"

    expected = (settings.app_api_token or "").strip()
    if expected:
        token = websocket.query_params.get("token") or websocket.headers.get(
            "x-app-token", ""
        )
        if not secure_compare(token, expected):
            return "Unauthorized"
    elif not is_dev_environment(settings.app_env):
        return "API token not configured"

    if not rate_limiter.allow(
        f"ws:{client_ip}", settings.rate_limit_expensive_per_minute, 60.0
    ):
        return "Too many requests"

    return None


@router.websocket("/ws/v1/conversations/{conversation_id}")
async def conversation_ws(websocket: WebSocket, conversation_id: str) -> None:
    denial = _authorize_ws(websocket)
    if denial:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=denial)
        return

    await websocket.accept()
    db: Session = SessionLocal()
    service = ConversationService(db)

    try:
        await websocket.send_json(
            {
                "type": "character_state",
                "state": "idle",
                "conversation_id": conversation_id,
            }
        )

        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                await websocket.send_json({"type": "character_state", "state": "listening"})
                await websocket.send_json({"type": "character_state", "state": "thinking"})

                result = await service.process_audio(conversation_id, message["bytes"])

                await websocket.send_json(
                    {"type": "user_transcript", "text": result.user_text}
                )
                await websocket.send_json(
                    {"type": "assistant_text", "text": result.assistant_text}
                )
                await websocket.send_json(
                    {"type": "character_state", "state": "talking"}
                )
                await websocket.send_json(
                    {
                        "type": "audio",
                        "url": result.stream_url or result.audio_url,
                        "latency": result.latency,
                    }
                )
                await websocket.send_json({"type": "character_state", "state": "idle"})
                continue

            raw = message.get("text")
            if not raw:
                continue

            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "set_state":
                await websocket.send_json(
                    {
                        "type": "character_state",
                        "state": data.get("state", "idle"),
                    }
                )
            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Unsupported event type: {msg_type}",
                    }
                )
    except WebSocketDisconnect:
        logger.info("WS disconnected conversation=%s", conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WS error conversation=%s", conversation_id)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
