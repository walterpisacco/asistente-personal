"""Parseo y despacho de acciones emitidas por el LLM.

El LLM puede anexar al final de su respuesta un JSON:
  {"clave":"ver_video","valor":"madonna"}
  {"clave":"agregar_recordatorio","valor":"llamar al médico","fecha":"2026-09-26 15:00"}

La tabla `actions` asocia `clave` → `metodo` (handler del backend).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.user import User
from app.repositories.action_repository import ActionRepository
from app.repositories.recordatory_repository import RecordatoryRepository
from app.services.youtube_service import YouTubeService

logger = logging.getLogger(__name__)

# Último objeto JSON {...} en la respuesta del LLM.
_ANY_JSON_OBJ_RE = re.compile(r"\{[^{}]+\}", re.DOTALL)

Handler = Callable[..., Awaitable["ActionResult"]]

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)


@dataclass
class ParsedLlmOutput:
    spoken_text: str
    clave: str | None = None
    valor: str | None = None
    fecha: str | None = None


@dataclass
class ActionResult:
    action: str
    metodo: str
    valor: str | None = None
    spoken_override: str | None = None
    end_session: bool = False
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "metodo": self.metodo,
            "valor": self.valor,
            "spoken_override": self.spoken_override,
            "end_session": self.end_session,
            "extra": self.extra or {},
        }


def parse_llm_output(raw: str) -> ParsedLlmOutput:
    """Separa texto para TTS del JSON de acción opcional."""
    text = (raw or "").strip()
    if not text:
        return ParsedLlmOutput(spoken_text="")

    matches = list(_ANY_JSON_OBJ_RE.finditer(text))
    for match in reversed(matches):
        chunk = match.group(0)
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        clave = data.get("clave")
        if not isinstance(clave, str) or not clave.strip():
            continue
        valor = data.get("valor", "")
        if valor is None:
            valor = ""
        elif isinstance(valor, (dict, list)):
            valor = json.dumps(valor, ensure_ascii=False)
        else:
            valor = str(valor).strip()
        fecha = data.get("fecha") or data.get("scheduled_at") or ""
        if fecha is None:
            fecha = ""
        else:
            fecha = str(fecha).strip()
        spoken = (text[: match.start()] + text[match.end() :]).strip()
        spoken = re.sub(r"\n{3,}", "\n\n", spoken).strip()
        return ParsedLlmOutput(
            spoken_text=spoken,
            clave=clave.strip().lower(),
            valor=valor,
            fecha=fecha or None,
        )

    return ParsedLlmOutput(spoken_text=text)


def parse_scheduled_at(raw: str | None) -> datetime | None:
    """Parsea fecha/hora flexible. None si vacío o inválida."""
    text = (raw or "").strip()
    if not text:
        return None
    # ISO con Z
    if text.endswith("Z"):
        text = text[:-1]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.warning("No pude parsear fecha de recordatorio: %r", raw)
        return None


def _parse_recordatory_valor(valor: str) -> tuple[str, datetime | None]:
    """valor plano o JSON {message, fecha|scheduled_at}."""
    text = (valor or "").strip()
    if not text:
        return "", None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text, None
        if isinstance(data, dict):
            message = str(
                data.get("message") or data.get("mensaje") or data.get("valor") or ""
            ).strip()
            fecha = data.get("fecha") or data.get("scheduled_at") or ""
            return message or text, parse_scheduled_at(str(fecha) if fecha else None)
    return text, None


class ActionDispatcher:
    """Resuelve clave → fila actions → método registrado."""

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = ActionRepository(db)
        self._handlers: dict[str, Handler] = {
            "ver_video": self._ver_video,
            "detener_video": self._detener_video,
            "finalizar": self._finalizar,
            "finalizar_llm": self._finalizar,
            "agregar_recordatorio": self._agregar_recordatorio,
            "recordatory": self._agregar_recordatorio,
            "dame_recordatorios": self._dame_recordatorios,
        }

    async def dispatch(
        self,
        *,
        clave: str,
        valor: str,
        user: Optional[User],
        user_text: str = "",
        fecha: str | None = None,
    ) -> ActionResult | None:
        row = self.repo.get_by_clave(clave)
        if row is None:
            logger.warning("Acción desconocida clave=%r (no está en tabla actions)", clave)
            return None

        metodo = (row.metodo or "").strip()
        handler = self._handlers.get(metodo)
        if handler is None:
            logger.error(
                "Método %r no registrado para clave=%r (actions.id=%s)",
                metodo,
                clave,
                row.id,
            )
            return ActionResult(
                action=clave,
                metodo=metodo,
                valor=valor,
                extra={"error": "handler_not_registered"},
            )

        logger.info(
            "Dispatch action clave=%s metodo=%s valor=%r fecha=%r",
            clave,
            metodo,
            valor,
            fecha,
        )
        return await handler(
            valor=valor,
            user=user,
            user_text=user_text,
            row_clave=clave,
            fecha=fecha,
        )

    async def _ver_video(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
    ) -> ActionResult:
        yt = YouTubeService(self.settings)
        result = await yt.play_from_profile(
            user=user,
            query_hint=valor or user_text,
            target=valor or "random",
        )
        return ActionResult(
            action=row_clave,
            metodo="ver_video",
            valor=valor,
            spoken_override=result.get("spoken"),
            extra=result,
        )

    async def _detener_video(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
    ) -> ActionResult:
        yt = YouTubeService(self.settings)
        result = yt.stop_playback()
        return ActionResult(
            action=row_clave,
            metodo="detener_video",
            valor=valor,
            spoken_override=result.get("spoken") or "Listo, paro la música.",
            extra=result,
        )

    async def _finalizar(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
    ) -> ActionResult:
        return ActionResult(
            action=row_clave,
            metodo="finalizar",
            valor=valor or "conversacion",
            end_session=True,
            extra={"reason": valor or "conversacion"},
        )

    async def _agregar_recordatorio(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=valor,
                spoken_override="No puedo guardar el recordatorio sin saber quién sos.",
                extra={"error": "no_user"},
            )

        message, scheduled_from_valor = _parse_recordatory_valor(valor)
        if not message:
            message = (user_text or "").strip()
        if not message:
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=valor,
                spoken_override="No entendí qué tengo que recordarte.",
                extra={"error": "empty_message"},
            )

        scheduled_at = scheduled_from_valor or parse_scheduled_at(fecha)
        row = RecordatoryRepository(self.db).create(
            user_id=int(user.id),
            message=message,
            scheduled_at=scheduled_at,
        )
        logger.info(
            "Recordatorio id=%s user=%s scheduled_at=%s message=%r",
            row.id,
            user.id,
            scheduled_at,
            message[:80],
        )
        if scheduled_at:
            spoken = f"Listo, te lo recuerdo el {scheduled_at.strftime('%d/%m a las %H:%M')}."
        else:
            spoken = "Listo, anoté el recordatorio."
        return ActionResult(
            action=row_clave,
            metodo="agregar_recordatorio",
            valor=message,
            spoken_override=spoken,
            extra={
                "id": row.id,
                "message": row.message,
                "scheduled_at": scheduled_at.isoformat(sep=" ") if scheduled_at else None,
            },
        )

    async def _dame_recordatorios(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="dame_recordatorios",
                valor=valor,
                spoken_override="No puedo listar recordatorios sin saber quién sos.",
                extra={"error": "no_user", "items": []},
            )

        rows = RecordatoryRepository(self.db).list_for_user(int(user.id))
        items = [
            {
                "id": row.id,
                "message": row.message,
                "scheduled_at": (
                    row.scheduled_at.isoformat(sep=" ") if row.scheduled_at else None
                ),
            }
            for row in rows
        ]
        if not items:
            spoken = "No tenés recordatorios pendientes."
        elif len(items) == 1:
            item = items[0]
            if item["scheduled_at"]:
                when = datetime.fromisoformat(item["scheduled_at"]).strftime(
                    "%d/%m a las %H:%M"
                )
                spoken = f"Tenés uno: {item['message']}, para el {when}."
            else:
                spoken = f"Tenés uno: {item['message']}."
        else:
            parts: list[str] = []
            for i, item in enumerate(items, start=1):
                if item["scheduled_at"]:
                    when = datetime.fromisoformat(item["scheduled_at"]).strftime(
                        "%d/%m a las %H:%M"
                    )
                    parts.append(f"{i}. {item['message']}, el {when}")
                else:
                    parts.append(f"{i}. {item['message']}")
            spoken = f"Tenés {len(items)} recordatorios. " + ". ".join(parts) + "."

        logger.info("Recordatorios user=%s count=%s", user.id, len(items))
        return ActionResult(
            action=row_clave,
            metodo="dame_recordatorios",
            valor=valor,
            spoken_override=spoken,
            extra={"count": len(items), "items": items},
        )


async def run_llm_action(
    db: Session,
    settings: Settings,
    *,
    raw_assistant_text: str,
    user: Optional[User],
    user_text: str = "",
) -> tuple[str, ActionResult | None]:
    """Parsea salida LLM, despacha acción si hay, devuelve (texto_tts, result)."""
    parsed = parse_llm_output(raw_assistant_text)
    spoken = parsed.spoken_text
    if not parsed.clave:
        return spoken, None

    dispatcher = ActionDispatcher(db, settings)
    result = await dispatcher.dispatch(
        clave=parsed.clave,
        valor=parsed.valor or "",
        user=user,
        user_text=user_text,
        fecha=parsed.fecha,
    )
    if result and result.spoken_override and not spoken:
        spoken = result.spoken_override
    elif result and result.spoken_override and result.metodo in (
        "ver_video",
        "dame_recordatorios",
        "agregar_recordatorio",
    ):
        # Preferir el spoken del sistema si el LLM no dijo nada útil.
        if len(spoken) < 8 or result.metodo == "dame_recordatorios":
            spoken = result.spoken_override
    return spoken, result
