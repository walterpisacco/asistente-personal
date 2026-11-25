"""Mini LLM que decide si el turno es una acción de backend o charla."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models.actions import Action
from app.models.user import User
from app.providers.llm.base import LLMProvider
from app.services.actions import (
    delete_messages_pending_confirm,
    delete_project_pending_confirm,
    delete_task_pending_confirm,
    extract_add_project_name,
    extract_add_task_parts,
    extract_delete_project_name,
    extract_delete_task_name,
    looks_like_timed_reminder,
    pending_add_task_query,
    pending_delete_messages_query,
    pending_delete_project_query,
    pending_delete_task_query,
    pending_intent_context,
    pending_list_tasks_query,
    wants_add_reminder,
    wants_add_task,
    wants_delete_messages,
    wants_delete_project,
    wants_delete_task,
    wants_list_projects,
    wants_list_tasks,
)

logger = logging.getLogger(__name__)

NONE = "none"

_ROUTER_SYSTEM = """
Sos un clasificador de intenciones para un asistente de voz en español (rioplatense).
Respondé SOLO un JSON con esta forma:
{"clave":"none","valor":"","valor1":"","fecha":"","confirm":null}

clave DEBE ser una de: {claves}
Si no hay que ejecutar nada en el backend, clave="none".

Catálogo:
{catalog}

Reglas:
- El proyecto seleccionado SOLO aplica a tareas/proyectos. Los RECORDATORIOS son del usuario conectado: no uses proyecto, no pidas proyecto, no pongas proyect_id.
- listar_tareas: el usuario quiere ver/mostrar/listar las tareas del proyecto actual. valor="".
- agregar_tarea: el usuario quiere crear una tarea. valor = nombre de la tarea. Si no dijo el nombre, valor="".
- eliminar_tarea: quiere borrar una tarea del proyecto seleccionado. valor = nombre de la tarea. Si no dijo el nombre, valor="". confirm=true/false solo si confirma o cancela un borrado ya propuesto.
- listar_proyectos: quiere ver sus proyectos. valor="".
- agregar_proyecto: quiere crear un proyecto. valor = nombre del proyecto.
- eliminar_proyecto: borrar un proyecto del usuario logueado (no del proyecto seleccionado). valor = nombre del proyecto. Si no dijo el nombre, valor="". confirm=true/false solo si confirma o cancela un borrado ya propuesto. Hay que avisar que también se borran tareas y conversaciones.
- eliminar_mensajes: borrar TODOS los mensajes del proyecto seleccionado (chat/conversación/historial). valor="". El backend usa el proyect_id del turno. No preguntar el proyecto. confirm=true/false solo si confirma o cancela un borrado ya propuesto. El borrado es físico.
- agregar_recordatorio: del usuario, no del proyecto. valor = texto a recordar (sin fecha ni recurrencia). La HORA es OBLIGATORIA siempre; si falta la hora, clave="none" (que el asistente pregunte).
  * Puntual: fecha = "YYYY-MM-DD HH:MM" (hora local Argentina) con FECHA_HORA_ACTUAL. Si dijo "mañana"/día de la semana/fecha sin año, resolvé el día absoluto. Si falta el día en un pedido puntual, clave="none". Ejemplo: {"clave":"agregar_recordatorio","valor":"llamar al dentista","fecha":"2026-10-30 16:30","recurrencia":"once"}.
  * Recurrente: recurrencia = daily | weekly | weekends. fecha = solo "HH:MM". Para weekly agregá dias (lunes, martes, … o 1-7 ISO). Ejemplos:
    - "recordame todos los días a las 9 tomar la pastilla" → {"clave":"agregar_recordatorio","valor":"tomar la pastilla","fecha":"09:00","recurrencia":"daily"}
    - "recordame todos los lunes a las 18 el standup" → {"clave":"agregar_recordatorio","valor":"el standup","fecha":"18:00","recurrencia":"weekly","dias":"lunes"}
    - "recordame los fines de semana a las 10 pasear" → {"clave":"agregar_recordatorio","valor":"pasear","fecha":"10:00","recurrencia":"weekends"}
- listar_recordatorios: listar los del usuario (no del proyecto). valor="".
- eliminar_recordatorio: valor=mensaje. Si dijo día y hora, fecha = "YYYY-MM-DD HH:MM". Si es recurrente, podés mandar recurrencia. confirm=true/false solo si confirma o cancela un borrado ya propuesto.
- finalizar: quiere cortar la conversación.
- Charla, saludos, opiniones, "¿ya lo anotaste?", repetir lo dicho: clave="none".
- Si hay un pending de agregar_tarea y el usuario dicta un nombre, clave="agregar_tarea" y valor=ese nombre.
- Si hay un pending de eliminar_tarea y el usuario dicta un nombre, clave="eliminar_tarea" y valor=ese nombre.
- Si hay pending de eliminar_tarea esperando confirmación y dice que sí, clave="eliminar_tarea" y confirm=true. Si cancela, confirm=false.
- Si hay un pending de eliminar_proyecto y el usuario dicta un nombre, clave="eliminar_proyecto" y valor=ese nombre.
- Si hay pending de eliminar_proyecto esperando confirmación y dice que sí, clave="eliminar_proyecto" y confirm=true. Si cancela, confirm=false.
- Si hay pending de eliminar_mensajes esperando confirmación y dice que sí, clave="eliminar_mensajes" y confirm=true. Si cancela, confirm=false.
- Si hay pending de agregar_recordatorio, este turno completa el mensaje o la fecha.
- "comprar vino para la cena mañana a las 7 de la tarde" ES agregar_recordatorio aunque no diga recordame. valor=comprar vino para la cena, fecha=YYYY-MM-DD 19:00.
- "de la tarde" suma 12 horas (7 de la tarde = 19:00).
- Mirà tambien los mensajes recientes: si el usuario ya pidió un recordatorio, este turno puede ser el texto o la fecha.
- No inventes claves. No expliques. Solo JSON.
""".strip()

_TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
_DIAS_ES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)


def build_router_system(claves: str, catalog: str, now=None) -> str:
    """System prompt del clasificador, con la fecha local para los recordatorios."""
    system = _ROUTER_SYSTEM.replace("{claves}", claves).replace("{catalog}", catalog)
    dt = now or datetime.now(_TZ_AR)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_AR)
    else:
        dt = dt.astimezone(_TZ_AR)
    clock = (
        "FECHA_HORA_ACTUAL:\n"
        f"- datetime: {dt.strftime('%Y-%m-%d %H:%M')}\n"
        f"- dia: {_DIAS_ES[dt.weekday()]}\n"
        "- timezone: America/Argentina/Buenos_Aires\n"
        "- Usá esta referencia para interpretar hoy, mañana, días de la semana y fechas sin año."
    )
    return f"{system}\n\n{clock}"


@dataclass
class IntentDecision:
    clave: str = NONE
    valor: str = ""
    valor1: str = ""
    fecha: str = ""
    recurrencia: str = ""
    dias: str = ""
    confirm: bool | None = None
    source: str = "none"

    @property
    def is_action(self) -> bool:
        clave = (self.clave or "").strip().lower()
        return bool(clave) and clave != NONE


class IntentRouter:
    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def classify(
        self,
        user_text: str,
        *,
        actions: list[Action],
        user: Optional[User] = None,
        db=None,
        proyect_id: int | None = None,
        proyect_name: str | None = None,
        pending: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> IntentDecision:
        allowed = {NONE, *(row.clave.strip().lower() for row in actions if row.clave)}
        pending = pending if pending is not None else pending_intent_context(user)

        if self._use_llm():
            try:
                timeout = max(1.0, float(self.settings.intent_timeout_seconds))
                data = await asyncio.wait_for(
                    self._classify_llm(
                        user_text,
                        actions=actions,
                        proyect_id=proyect_id,
                        proyect_name=proyect_name,
                        pending=pending,
                        history=history,
                    ),
                    timeout=timeout,
                )
                decision = self._from_payload(data, allowed, source="router")
                logger.info(
                    "Intent router clave=%s valor=%r fecha=%r recurrencia=%r dias=%r proyect=%s name=%r",
                    decision.clave,
                    decision.valor,
                    decision.fecha,
                    decision.recurrencia,
                    decision.dias,
                    proyect_id,
                    proyect_name,
                )
                return decision
            except Exception:
                logger.exception("Intent router failed, using regex fallback")

        decision = classify_with_regex(
            user_text,
            proyect_name=proyect_name,
            user=user,
            db=db,
            history=history,
        )
        logger.info(
            "Intent regex clave=%s valor=%r proyect=%s name=%r",
            decision.clave,
            decision.valor,
            proyect_id,
            proyect_name,
        )
        return decision

    def _use_llm(self) -> bool:
        return self.settings.llm_provider.lower() in ("openai", "ollama")

    async def _classify_llm(
        self,
        user_text: str,
        *,
        actions: list[Action],
        proyect_id: int | None,
        proyect_name: str | None,
        pending: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        claves = ", ".join([NONE, *(row.clave for row in actions if row.clave)])
        catalog = json.dumps(
            [
                {
                    "clave": row.clave,
                    "description": (row.description or "")[:180],
                }
                for row in actions
            ],
            ensure_ascii=False,
        )
        system = build_router_system(claves, catalog)
        recent = []
        for msg in (history or [])[-8:]:
            role = str(msg.get("role") or "")
            content = str(msg.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                recent.append({"role": role, "content": content[:240]})
        payload = {
            "tareas": {
                "proyecto_id": proyect_id,
                "proyecto_nombre": proyect_name,
            },
            "recordatorios": "del usuario conectado; no usar el proyecto",
            "pending": pending or {},
            "historial_reciente": recent,
            "usuario": user_text,
        }
        logger.info("Intent router calling mini LLM text=%r pending=%s", user_text, bool(pending))
        return await self.llm.generate_json(
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            system,
            temperature=0,
            max_tokens=200,
        )

    @staticmethod
    def _from_payload(
        data: dict[str, Any],
        allowed: set[str],
        *,
        source: str,
    ) -> IntentDecision:
        clave = str(data.get("clave") or NONE).strip().lower()
        if clave not in allowed:
            clave = NONE
        valor = data.get("valor")
        valor1 = data.get("valor1")
        fecha = data.get("fecha")
        recurrencia = data.get("recurrencia") or data.get("recurrence") or ""
        dias = data.get("dias") or data.get("weekdays") or ""
        confirm_raw = data.get("confirm")
        confirm: bool | None
        if isinstance(confirm_raw, bool):
            confirm = confirm_raw
        elif isinstance(confirm_raw, str) and confirm_raw.strip():
            confirm = confirm_raw.strip().lower() in {
                "1",
                "true",
                "si",
                "sí",
                "yes",
                "ok",
                "dale",
            }
        else:
            confirm = None
        return IntentDecision(
            clave=clave,
            valor="" if valor is None else str(valor).strip(),
            valor1="" if valor1 is None else str(valor1).strip(),
            fecha=_normalize_router_fecha("" if fecha is None else str(fecha).strip()),
            recurrencia="" if recurrencia is None else str(recurrencia).strip().lower(),
            dias="" if dias is None else str(dias).strip().lower(),
            confirm=confirm,
            source=source,
        )


def classify_with_regex(
    user_text: str,
    *,
    proyect_name: str | None = None,
    user: Optional[User] = None,
    db=None,
    history: list[dict[str, Any]] | None = None,
) -> IntentDecision:
    pending_add = pending_add_task_query(db, user, user_text) if db is not None else None
    pending_list = (
        pending_list_tasks_query(db, user, user_text) if db is not None else None
    )
    pending_del = pending_delete_task_query(db, user, user_text)
    pending_del_proj = pending_delete_project_query(db, user, user_text)
    pending_del_msgs = pending_delete_messages_query(db, user, user_text)
    if wants_list_projects(user_text):
        return IntentDecision(clave="listar_proyectos", source="regex")
    if wants_delete_messages(user_text) or pending_del_msgs:
        confirm = (
            delete_messages_pending_confirm(user, user_text)
            if pending_del_msgs
            else None
        )
        return IntentDecision(
            clave="eliminar_mensajes",
            confirm=confirm,
            source="regex",
        )
    if wants_delete_project(user_text) or pending_del_proj:
        confirm = (
            delete_project_pending_confirm(user, user_text) if pending_del_proj else None
        )
        return IntentDecision(
            clave="eliminar_proyecto",
            valor=extract_delete_project_name(user_text) or (pending_del_proj or ""),
            confirm=confirm,
            source="regex",
        )
    if wants_delete_task(user_text) or pending_del:
        ext_name = extract_delete_task_name(user_text)
        pending_name = (pending_del or ("", ""))[0]
        confirm = delete_task_pending_confirm(user, user_text) if pending_del else None
        return IntentDecision(
            clave="eliminar_tarea",
            valor=ext_name or pending_name,
            confirm=confirm,
            source="regex",
        )
    if wants_add_task(user_text) or pending_add:
        ext_name, _ext_proj = extract_add_task_parts(user_text)
        pending_name = (pending_add or ("", ""))[0]
        return IntentDecision(
            clave="agregar_tarea",
            valor=ext_name or pending_name,
            source="regex",
        )
    if wants_list_tasks(user_text, proyect_name) or pending_list:
        return IntentDecision(clave="listar_tareas", source="regex")
    pending_rem = None
    if user is not None:
        from app.services.actions import _pending_add_reminder

        pending_rem = _pending_add_reminder.get(int(user.id))
    if (
        wants_add_reminder(user_text)
        or looks_like_timed_reminder(user_text)
        or pending_rem
        or _history_suggests_reminder(history)
    ):
        valor = (pending_rem or {}).get("message") or ""
        return IntentDecision(
            clave="agregar_recordatorio",
            valor=valor,
            source="regex",
        )
    name = extract_add_project_name(user_text)
    if name:
        return IntentDecision(clave="agregar_proyecto", valor=name, source="regex")
    return IntentDecision(clave=NONE, source="regex")


def _history_suggests_reminder(history: list[dict[str, Any]] | None) -> bool:
    for msg in reversed((history or [])[-8:]):
        text = str(msg.get("content") or "")
        if not text.strip():
            continue
        if wants_add_reminder(text) or looks_like_timed_reminder(text):
            return True
        if msg.get("role") == "assistant" and re.search(
            r"(?i)recordatorio|d[ií]a y hora|te lo recuerdo",
            text,
        ):
            return True
    return False


def _normalize_router_fecha(raw: str) -> str:
    """Normaliza fecha ISO o deja HH:MM para recordatorios recurrentes."""
    text = (raw or "").strip()
    if not text:
        return ""
    # Solo hora (recurrente): 9:00 / 09:00
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = text.split(":", 1)
        return f"{int(hour):02d}:{int(minute):02d}"
    if re.fullmatch(r"\d{1,2}", text):
        return f"{int(text):02d}:00"
    cleaned = text.replace("T", " ").replace("Z", "").strip()
    if "." in cleaned:
        cleaned = cleaned.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return text
