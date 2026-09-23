"""Parseo y despacho de acciones emitidas por el LLM.

El LLM puede anexar al final de su respuesta un JSON:
  {"clave":"ver_video","valor":"madonna"}

La tabla `actions` asocia `clave` → `metodo` (handler del backend).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.user import User
from app.repositories.action_repository import ActionRepository
from app.services.youtube_service import YouTubeService

logger = logging.getLogger(__name__)

# Último objeto JSON {...} en la respuesta del LLM.
_ANY_JSON_OBJ_RE = re.compile(r"\{[^{}]+\}", re.DOTALL)

Handler = Callable[..., Awaitable["ActionResult"]]


@dataclass
class ParsedLlmOutput:
    spoken_text: str
    clave: str | None = None
    valor: str | None = None


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
        spoken = (text[: match.start()] + text[match.end() :]).strip()
        # Limpiar líneas vacías residuales
        spoken = re.sub(r"\n{3,}", "\n\n", spoken).strip()
        return ParsedLlmOutput(
            spoken_text=spoken,
            clave=clave.strip().lower(),
            valor=str(valor).strip(),
        )

    return ParsedLlmOutput(spoken_text=text)


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
        }

    async def dispatch(
        self,
        *,
        clave: str,
        valor: str,
        user: Optional[User],
        user_text: str = "",
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
            "Dispatch action clave=%s metodo=%s valor=%r",
            clave,
            metodo,
            valor,
        )
        return await handler(valor=valor, user=user, user_text=user_text, row_clave=clave)

    async def _ver_video(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
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
    ) -> ActionResult:
        return ActionResult(
            action=row_clave,
            metodo="finalizar",
            valor=valor or "conversacion",
            end_session=True,
            extra={"reason": valor or "conversacion"},
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
    )
    if result and result.spoken_override and not spoken:
        spoken = result.spoken_override
    elif result and result.spoken_override and result.metodo == "ver_video":
        # Preferir el spoken del sistema si el LLM no dijo nada útil.
        if len(spoken) < 8:
            spoken = result.spoken_override
    return spoken, result
