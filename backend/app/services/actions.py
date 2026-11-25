"""Parseo y despacho de acciones emitidas por el LLM.

El LLM puede anexar al final de su respuesta un JSON:
  {"clave":"agregar_recordatorio","valor":"llamar al médico","fecha":"mañana 16:30"}

La tabla `actions` asocia `clave` → `metodo` (handler del backend).
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.recordatory import Recordatory
from app.models.tasks import Tasks
from app.models.user import User
from app.models.proyects import Proyects
from app.repositories.action_repository import ActionRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.recordatory_repository import RecordatoryRepository
from app.repositories.proyects_repository import (
    DEFAULT_PROJECT_NAME,
    ProyectsRepository,
    is_default_project_name,
)
from app.repositories.tasks_repository import TasksRepository
from app.services.recordatory_recurrence import (
    RECURRENCE_ONCE,
    RecurrenceSpec,
    describe_recurrence,
    next_occurrence,
    normalize_time_of_day,
    parse_time_of_day,
    resolve_recurrence,
    spec_from_row,
    strip_recurrence_fragment,
)
from app.services.youtube_service import YouTubeService

logger = logging.getLogger(__name__)

# Último objeto JSON {...} en la respuesta del LLM.
_ANY_JSON_OBJ_RE = re.compile(r"\{[^{}]+\}", re.DOTALL)

Handler = Callable[..., Awaitable["ActionResult"]]

_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
_DEFAULT_HOUR = 9
_DEFAULT_MINUTE = 0

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
    valor1: str | None = None
    fecha: str | None = None
    recurrencia: str | None = None
    dias: str | None = None
    confirm: bool | None = None


# Pendiente de confirmación de borrado: user_id → recordatory_id
_pending_delete: dict[int, int] = {}

_AFFIRM_RE = re.compile(
    r"(?i)^\s*(sí|si|dale|ok|okay|confirmo|confirmá|confirma|"
    r"borralo|eliminalo|hacelo|de\s+acuerdo|adelante)\b"
)
_DENY_RE = re.compile(
    r"(?i)^\s*(no|cancelá|cancela|cancelar|dejá|deja|mejor\s+no)\b"
)

_LIST_PROJECTS_RE = re.compile(
    r"(?i)\b("
    r"lista(me|rme)?|listá|listar|"
    r"mostr(a|ame|ame)|mostrá|"
    r"cu[aá]les?\s+son|"
    r"qu[eé]\s+proyectos"
    r")\b.*\bproyectos?\b"
    r"|\bproyectos?\b.*\b(lista|listá|listar|ten[eé]s|tengo)\b"
)

_LIST_TASK_VERBS = (
    r"lista(?:me|rme)?|listá|listar|"
    r"mostr(?:arme|ame|ar|á|a)|mu[eé]str(?:ame|a)|"
    r"dame|d[eé]cime|dime|dec[ií]|"
    r"ver|ves|"
    r"cu[aá]les?\s+son|"
    r"otra\s+vez|de\s+nuevo|repet[ií]"
)

_LIST_TASKS_RE = re.compile(
    r"(?i)"
    r"(?:"
    rf"\b(?:{_LIST_TASK_VERBS})\b.*\btareas?\b"
    r"|\bqu[eé]\s+tareas?\b"
    r"|\btareas?\b.*\b(?:lista|listá|listar|ten[eé]s|tengo|otra\s+vez|de\s+nuevo)\b"
    r"|^\s*(?:(?:las?|mis|las\s+mismas)\s+)?tareas?\s*[.!?]*\s*$"
    r"|\btareas\s+(?:de|del|para)\b"
    r")"
)

_BARE_LIST_RE = re.compile(
    r"(?i)^\s*(?:por\s+favor\s+)?"
    r"(?:lista(?:me|rme)?|listá|listar|mostr(?:arme|ame|ar|á|a)|mu[eé]str(?:ame|a)|"
    r"dame|d[eé]cime|dime|dec[ií])"
    r"(?:\s+(?:las?|los|mis|eso|esas|estos?))?"
    r"(?:\s+tareas?)?"
    r"(?:\s+por\s+favor)?"
    r"\s*[.!?]*\s*$"
)

_LIST_HINT_RE = re.compile(
    r"(?i)\b(?:lista(?:me|rme)?|listá|listar|mostr(?:arme|ame|ar|á|a)|mu[eé]str(?:ame|a)|"
    r"dame|d[eé]cime|dime|dec[ií]|ver|ves|tareas?|otra\s+vez|de\s+nuevo|repet[ií])\b"
)

_TASK_PROJECT_RE = re.compile(
    r"(?i)\btareas?\b(?:\s+(?:pendientes|activas|abiertas|por\s+hacer))?\s+"
    r"(?:de(?:l)?(?:\s+proyecto)?|para(?:\s+el\s+proyecto)?)\s+(.+?)\s*$"
)

_COURTESY_TAIL_RE = re.compile(
    r"(?i)\s+(por\s+favor|please|che|dale|gracias)\s*[.!?]*$"
)

# user_id → esperando el nombre del proyecto para listar_tareas
_pending_list_tasks: dict[int, bool] = {}


def wants_list_projects(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if re.search(r"(?i)\btareas?\b", text):
        return False
    if wants_delete_project(text):
        return False
    return bool(_LIST_PROJECTS_RE.search(text))


def _mentions_selected_project(text: str, project_name: str) -> bool:
    name = (project_name or "").strip()
    if not name or not (text or "").strip():
        return False
    if _project_match_score(text, name) >= 0.8:
        return True
    needle = _strip_project_noise(name)
    haystack = _normalize_msg(text)
    if needle and needle in haystack:
        return True
    tokens = [
        tok
        for tok in re.split(r"\W+", needle or _normalize_msg(name))
        if tok and not tok.isdigit() and len(tok) > 2
    ]
    return bool(tokens) and all(tok in haystack for tok in tokens)


def wants_list_tasks(user_text: str, project_name: str | None = None) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if _ADD_TASK_INTENT_RE.search(text) or _DELETE_TASK_INTENT_RE.search(text):
        return False
    if _LIST_TASKS_RE.search(text) or _BARE_LIST_RE.search(text):
        return True
    if project_name and _LIST_HINT_RE.search(text) and _mentions_selected_project(
        text, project_name
    ):
        return True
    return False


def extract_task_project(user_text: str) -> str:
    """Saca el nombre del proyecto de frases tipo 'tareas de mi casa'."""
    text = (user_text or "").strip()
    if not text:
        return ""
    match = _TASK_PROJECT_RE.search(text)
    if not match:
        return ""
    raw = match.group(1).strip().strip(".,;:!?")
    raw = _COURTESY_TAIL_RE.sub("", raw).strip().strip(".,;:!?")
    raw = re.sub(
        r"(?i)^(el|la|los|las|mi|mis|del|al)\s+proyecto\s+",
        "",
        raw,
    ).strip()
    raw = re.sub(r"(?i)^proyecto\s+", "", raw).strip()
    if raw.lower() in {"el", "la", "los", "las", "mi", "mis", "un", "una"}:
        return ""
    return raw


_ADD_PROJECT_RE = re.compile(
    r"(?i)\b(?:agreg(?:a|á|ame|ar)|cre(?:a|á|ame|ar)|anot(?:a|á|ame|ar)|nuevo)\b"
    r".*\bproyectos?\b(?:\s+(?:llamad[oa]|que\s+se\s+llame))?\s*[:\-]?\s*(.+)$"
)


def _clean_project_name(name: str) -> str:
    cleaned = (name or "").strip().strip(".,;:!?\"'")
    cleaned = _COURTESY_TAIL_RE.sub("", cleaned).strip().strip(".,;:!?\"'")
    cleaned = re.sub(r"(?i)^(el|la|un|una)\s+proyecto\s+", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^proyecto\s+", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^(de|del)\s+", "", cleaned).strip()
    if cleaned.lower() in {"el", "la", "los", "las", "mi", "mis", "un", "una", "de", "del"}:
        return ""
    return cleaned[:500]


def extract_add_project_name(user_text: str) -> str:
    """Saca el nombre de frases tipo 'agregá un proyecto mi casa'."""
    text = (user_text or "").strip()
    if not text:
        return ""
    match = _ADD_PROJECT_RE.search(text)
    if not match:
        return ""
    return _clean_project_name(match.group(1))


_ADD_TASK_VERB = (
    r"(?:agreg(?:arle|arme|ame|ás|as|á|a|ar)|"
    r"cre(?:arme|ame|ás|as|á|a|ar)|"
    r"anot(?:arme|ame|ás|as|á|a|ar)|"
    r"añad(?:irme|ime|ís|is|í|e|ir)|"
    r"nueva)"
)

_ADD_TASK_INTENT_RE = re.compile(
    rf"(?i)\b{_ADD_TASK_VERB}\b.*\btareas?\b"
)

_ADD_TASK_RE = re.compile(
    rf"(?i)\b{_ADD_TASK_VERB}\b"
    r".*\btareas?\b(?:\s+(?:llamad[oa]|que\s+se\s+llame))?\s*[:\-]?\s*(.+)$"
)

_ADD_TASK_PROJECT_SPLIT_RE = re.compile(
    r"(?i)(?:^|\s+)(?:al|en|del|para)(?:\s+el|\s+la)?\s+proyecto\s+(.+)$"
)

# user_id → {name, proyect_ref} mientras falta un dato de agregar_tarea
_pending_add_task: dict[int, dict[str, str]] = {}
# user_id → {name, proyect_ref, task_id?} mientras falta nombre o confirmación
_pending_delete_task: dict[int, dict[str, Any]] = {}
# user_id → {message, recurrencia?, dias?, time_of_day?} mientras falta dato
_pending_add_reminder: dict[int, dict[str, str]] = {}

_DELETE_TASK_VERB = (
    r"(?:elimin(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"borr(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"sac(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"quit(?:arlo|arla|ame|ás|as|á|a|ar))"
)

_DELETE_TASK_INTENT_RE = re.compile(
    rf"(?i)\b{_DELETE_TASK_VERB}\b.*\btareas?\b"
    r"|\btareas?\b.*\b(?:elimin|borr|sac[aá]|quit)"
)

_DELETE_TASK_RE = re.compile(
    rf"(?i)\b{_DELETE_TASK_VERB}\b"
    r".*\btareas?\b(?:\s+(?:llamad[oa]|que\s+se\s+llame))?\s*[:\-]?\s*(.+)$"
)

_DELETE_PROJECT_INTENT_RE = re.compile(
    rf"(?i)\b{_DELETE_TASK_VERB}\b.*\bproyectos?\b"
    r"|\bproyectos?\b.*\b(?:elimin|borr|sac[aá]|quit)"
)

_DELETE_PROJECT_RE = re.compile(
    rf"(?i)\b{_DELETE_TASK_VERB}\b"
    r".*\bproyectos?\b(?:\s+(?:llamad[oa]|que\s+se\s+llame))?\s*[:\-]?\s*(.*)$"
)

# user_id → {name, proyect_id?} mientras falta nombre o confirmación
_pending_delete_project: dict[int, dict[str, Any]] = {}
# user_id → {proyect_id} mientras falta confirmación de borrar mensajes
_pending_delete_messages: dict[int, dict[str, Any]] = {}

_MESSAGE_NOUN_RE = re.compile(
    r"(?i)\b(?:mensajes?|conversaci[oó]n(?:es)?|chats?|historial)\b"
)
_CLEAR_MESSAGES_VERB_RE = re.compile(
    rf"(?i)\b(?:{_DELETE_TASK_VERB}|"
    r"limpi(?:arlos|arlo|ame|ás|as|á|a|ar)|"
    r"vac(?:iá|iar|iame|iálo|ialo))\b"
)


def _clean_task_name(name: str) -> str:
    cleaned = (name or "").strip().strip(".,;:!?\"'")
    cleaned = _COURTESY_TAIL_RE.sub("", cleaned).strip().strip(".,;:!?\"'")
    cleaned = re.sub(r"(?i)^(una?|la|el|las|los)\s+tarea\s+", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^tarea\s+", "", cleaned).strip()
    if cleaned.lower() in {"el", "la", "los", "las", "una", "un", "mi", "mis"}:
        return ""
    return cleaned[:5000]


def extract_add_task_parts(user_text: str) -> tuple[str, str]:
    """Devuelve (nombre_tarea, proyecto) de frases tipo 'agregá una tarea X al proyecto Y'."""
    text = (user_text or "").strip()
    if not text:
        return "", ""
    match = _ADD_TASK_RE.search(text)
    if not match:
        return "", ""
    rest = match.group(1).strip()
    split = _ADD_TASK_PROJECT_SPLIT_RE.search(rest)
    if split:
        name = rest[: split.start()].strip()
        project = split.group(1).strip()
        return _clean_task_name(name), _clean_project_name(project)
    return _clean_task_name(rest), ""


def wants_add_task(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text or wants_list_tasks(text) or wants_list_projects(text):
        return False
    if wants_delete_task(text):
        return False
    return bool(_ADD_TASK_INTENT_RE.search(text))


def extract_delete_task_name(user_text: str) -> str:
    """Saca el nombre de frases tipo 'borrá la tarea comprar pan'."""
    text = (user_text or "").strip()
    if not text:
        return ""
    match = _DELETE_TASK_RE.search(text)
    if not match:
        return ""
    rest = match.group(1).strip()
    split = _ADD_TASK_PROJECT_SPLIT_RE.search(rest)
    if split:
        rest = rest[: split.start()].strip()
    return _clean_task_name(rest)


def wants_delete_messages(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if re.search(r"(?i)\brecordatorios?\b", text):
        return False
    if re.search(r"(?i)\btareas?\b", text):
        return False
    if not _MESSAGE_NOUN_RE.search(text):
        return False
    return bool(_CLEAR_MESSAGES_VERB_RE.search(text))


def wants_delete_task(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if re.search(r"(?i)\brecordatorios?\b", text):
        return False
    if wants_delete_messages(text):
        return False
    if wants_delete_project(text):
        return False
    if wants_list_projects(text):
        return False
    return bool(_DELETE_TASK_INTENT_RE.search(text))


def extract_delete_project_name(user_text: str) -> str:
    """Saca el nombre de frases tipo 'borrá el proyecto mi casa'."""
    text = (user_text or "").strip()
    if not text:
        return ""
    match = _DELETE_PROJECT_RE.search(text)
    if not match:
        return ""
    return _clean_project_name(match.group(1) or "")


_SELECT_PROJECT_VERB = (
    r"(?:seleccion(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"eleg(?:irlo|irla|ime|ís|is|í|i|ir)|"
    r"cambi(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"pas(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"activ(?:arlo|arla|ame|ás|as|á|a|ar)|"
    r"pon(?:erlo|erla|eme|és|es|é|e|er))"
)

_SELECT_PROJECT_INTENT_RE = re.compile(
    rf"(?i)\b{_SELECT_PROJECT_VERB}\b.*\bproyectos?\b"
    r"|\bproyectos?\b.*\b(?:seleccion|eleg[ií]|cambi[aá]|pas[aá]|activ)"
)

_SELECT_PROJECT_RE = re.compile(
    rf"(?i)\b{_SELECT_PROJECT_VERB}\b"
    r".*\bproyectos?\b(?:\s+(?:llamad[oa]|que\s+se\s+llame))?\s*[:\-]?\s*(.*)$"
)


def extract_select_project_name(user_text: str) -> str:
    """Saca el nombre de frases tipo 'seleccioná el proyecto mi casa'."""
    text = (user_text or "").strip()
    if not text:
        return ""
    match = _SELECT_PROJECT_RE.search(text)
    if not match:
        return ""
    return _clean_project_name(match.group(1) or "")


def wants_select_project(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if wants_delete_project(text):
        return False
    if wants_list_projects(text):
        return False
    if re.search(r"(?i)\b(?:agreg|cre|añad|nueva).*\bproyectos?\b", text):
        return False
    return bool(_SELECT_PROJECT_INTENT_RE.search(text))


def wants_delete_project(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if re.search(r"(?i)\brecordatorios?\b", text):
        return False
    if re.search(r"(?i)\btareas?\b", text):
        return False
    if wants_delete_messages(text):
        return False
    return bool(_DELETE_PROJECT_INTENT_RE.search(text))


def pending_delete_project_query(
    _db: Session,
    user: Optional[User],
    user_text: str,
) -> str | None:
    """Completa el nombre si el bot preguntó cuál proyecto borrar."""
    if user is None:
        return None
    pending = _pending_delete_project.get(int(user.id))
    if not pending:
        return None
    name = str(pending.get("name") or "").strip()
    if not name:
        name = extract_delete_project_name(user_text) or _clean_project_name(
            user_text or ""
        )
    return name


def delete_project_pending_confirm(
    user: Optional[User],
    user_text: str,
) -> bool | None:
    """True/False si hay un borrado de proyecto pendiente y el turno afirma o cancela."""
    if user is None:
        return None
    pending = _pending_delete_project.get(int(user.id)) or {}
    if not pending.get("proyect_id"):
        return None
    text = (user_text or "").strip()
    if _DENY_RE.search(text):
        return False
    if _AFFIRM_RE.search(text):
        return True
    return None


def pending_delete_messages_query(
    _db: Session,
    user: Optional[User],
    _user_text: str,
) -> bool:
    if user is None:
        return False
    pending = _pending_delete_messages.get(int(user.id)) or {}
    return pending.get("proyect_id") is not None


def delete_messages_pending_confirm(
    user: Optional[User],
    user_text: str,
) -> bool | None:
    if user is None:
        return None
    pending = _pending_delete_messages.get(int(user.id)) or {}
    if pending.get("proyect_id") is None:
        return None
    text = (user_text or "").strip()
    if _DENY_RE.search(text):
        return False
    if _AFFIRM_RE.search(text):
        return True
    return None


def pending_delete_task_query(
    _db: Session,
    user: Optional[User],
    user_text: str,
) -> tuple[str, str] | None:
    """Completa el nombre si el bot preguntó cuál tarea borrar."""
    if user is None:
        return None
    pending = _pending_delete_task.get(int(user.id))
    if not pending:
        return None
    name = str(pending.get("name") or "").strip()
    proyect_ref = str(pending.get("proyect_ref") or "").strip()
    if not name:
        name = extract_delete_task_name(user_text) or _clean_task_name(user_text or "")
    return name, proyect_ref


def delete_task_pending_confirm(
    user: Optional[User],
    user_text: str,
) -> bool | None:
    """True/False si hay un borrado de tarea pendiente y el turno afirma o cancela."""
    if user is None:
        return None
    pending = _pending_delete_task.get(int(user.id)) or {}
    if not pending.get("task_id"):
        return None
    text = (user_text or "").strip()
    if _DENY_RE.search(text):
        return False
    if _AFFIRM_RE.search(text):
        return True
    return None


def pending_add_task_query(
    _db: Session,
    user: Optional[User],
    user_text: str,
) -> tuple[str, str] | None:
    """Completa el nombre si el bot preguntó cómo se llama la tarea."""
    if user is None:
        return None
    pending = _pending_add_task.get(int(user.id))
    if not pending:
        return None
    name = (pending.get("name") or "").strip()
    proyect_ref = (pending.get("proyect_ref") or "").strip()
    ext_name, ext_proj = extract_add_task_parts(user_text)
    if not name:
        name = ext_name or _clean_task_name(user_text or "")
    return name, proyect_ref or ext_proj


def _strip_project_noise(text: str) -> str:
    cleaned = _normalize_msg(text)
    cleaned = re.sub(
        r"(?i)^(el|la|los|las|mi|mis|tu|tus|nuestro|nuestra|del|al)\s+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)^(proyecto|proyectos)\s+", "", cleaned)
    cleaned = re.sub(r"(?i)\s+(proyecto|proyectos)$", "", cleaned)
    return cleaned.strip()


def _significant_tokens(text: str) -> list[str]:
    return [
        tok
        for tok in re.split(r"\W+", _normalize_msg(text))
        if tok and not tok.isdigit() and len(tok) > 2
    ]


def _project_match_score(query: str, name: str) -> float:
    q = _normalize_msg(query)
    n = _normalize_msg(name)
    q2 = _strip_project_noise(query)
    n2 = _strip_project_noise(name)
    if q and q == n:
        return 1.0
    if q2 and q2 == n2:
        return 0.95
    if q and (q in n or n in q):
        return 0.85
    if q2 and (q2 in n2 or n2 in q2):
        return 0.8
    name_tokens = _significant_tokens(n2 or n)
    query_norm = q2 or q
    if name_tokens and all(tok in query_norm for tok in name_tokens):
        return 0.82
    token_score = _message_match_score(q2 or q, n2 or n)
    return token_score * 0.7 if token_score > 0 else 0.0


def _match_projects(projects: list[Proyects], query: str) -> list[Proyects]:
    scored: list[tuple[float, Proyects]] = []
    for row in projects:
        score = _project_match_score(query, row.name or "")
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    if not scored:
        return []
    best = scored[0][0]
    return [row for score, row in scored if score >= best - 0.01]


def pending_list_tasks_query(
    db: Session,
    user: Optional[User],
    user_text: str,
) -> str | None:
    """Si el bot preguntó el proyecto y el texto matchea uno, devolver el query."""
    if user is None:
        return None
    user_id = int(user.id)
    if user_id not in _pending_list_tasks:
        return None
    query = (extract_task_project(user_text) or user_text or "").strip()
    if not query or len(query) > 120:
        return None
    projects = ProyectsRepository(db).list_for_user(user_id)
    if _match_projects(projects, query):
        return query
    return None


def pending_intent_context(user: Optional[User]) -> dict[str, Any]:
    """Estado pending para el router de intenciones."""
    if user is None:
        return {}
    uid = int(user.id)
    ctx: dict[str, Any] = {}
    add = _pending_add_task.get(uid)
    if add:
        ctx["agregar_tarea"] = dict(add)
    delete_task = _pending_delete_task.get(uid)
    if delete_task:
        ctx["eliminar_tarea"] = dict(delete_task)
    delete_project = _pending_delete_project.get(uid)
    if delete_project:
        ctx["eliminar_proyecto"] = dict(delete_project)
    delete_messages = _pending_delete_messages.get(uid)
    if delete_messages:
        ctx["eliminar_mensajes"] = dict(delete_messages)
    if uid in _pending_list_tasks:
        ctx["listar_tareas"] = True
    rem = _pending_add_reminder.get(uid)
    if rem:
        ctx["agregar_recordatorio"] = dict(rem)
    return ctx


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
        if not valor:
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                valor = name.strip()
        valor1 = data.get("valor1", "")
        if valor1 is None:
            valor1 = ""
        elif isinstance(valor1, (int, float)):
            valor1 = str(int(valor1)) if float(valor1).is_integer() else str(valor1)
        else:
            valor1 = str(valor1).strip()
        if not valor1:
            for key in ("proyect_id", "proyecto_id", "project_id"):
                raw = data.get(key)
                if raw is None:
                    continue
                token = str(raw).strip()
                if token:
                    valor1 = token
                    break
        fecha = data.get("fecha") or data.get("scheduled_at") or ""
        if fecha is None:
            fecha = ""
        else:
            fecha = str(fecha).strip()
        recurrencia = data.get("recurrencia") or data.get("recurrence") or ""
        if recurrencia is None:
            recurrencia = ""
        else:
            recurrencia = str(recurrencia).strip().lower()
        dias = data.get("dias") or data.get("weekdays") or ""
        if dias is None:
            dias = ""
        else:
            dias = str(dias).strip().lower()
        confirm_raw = data.get("confirm")
        confirm: bool | None
        if isinstance(confirm_raw, bool):
            confirm = confirm_raw
        elif isinstance(confirm_raw, (int, float)):
            confirm = bool(confirm_raw)
        elif isinstance(confirm_raw, str):
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
        spoken = (text[: match.start()] + text[match.end() :]).strip()
        spoken = re.sub(r"\n{3,}", "\n\n", spoken).strip()
        return ParsedLlmOutput(
            spoken_text=spoken,
            clave=clave.strip().lower(),
            valor=valor,
            valor1=valor1 or None,
            fecha=fecha or None,
            recurrencia=recurrencia or None,
            dias=dias or None,
            confirm=confirm,
        )

    return ParsedLlmOutput(spoken_text=text)


def _naive_local(dt: datetime) -> datetime:
    """Normaliza a datetime naive en zona AR (así se guarda en MySQL)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TZ).replace(tzinfo=None)
    return dt


def _now_local() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def _apply_default_time(dt: datetime) -> datetime:
    """Si vino solo fecha (00:00), usar 09:00 local."""
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt.replace(hour=_DEFAULT_HOUR, minute=_DEFAULT_MINUTE)
    return dt


_EXPLICIT_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Turnos que confirman/preguntan por un recordatorio ya hecho (no crear de nuevo).
_REMINDER_CONFIRM_RE = re.compile(
    r"(?i)\b("
    r"lo\s+(anotaste|hiciste|guardaste|creaste|agregaste|apuntaste)"
    r"|ya\s+lo\s+(anotaste|hiciste|guardaste|ten[eé]s|tenias|tenías)"
    r"|est[aá]\s+(anotado|guardado|listo)"
    r"|me\s+lo\s+(anotaste|recordaste|guardaste)"
    r"|lo\s+ten[eé]s\s+anotado"
    r"|ya\s+est[aá]"
    r")\b"
)
# Intención clara de crear un recordatorio nuevo en este turno.
_REMINDER_CREATE_RE = re.compile(
    r"(?i)\b("
    r"record(?:ame|ame|ás|as|á|a|ar)|acordate|"
    r"anot(?:ame|alo|ala|ás|as|á|a|ar)|"
    r"agreg(?:ame|alo|ala|ás|as|á|a|ar).{0,40}recordatorio|"
    r"pon(?:eme|é|e|er).{0,20}recordatorio|"
    r"apunt(?:ame|alo|ala|ás|as|á|a|ar).{0,20}recordatorio|"
    r"quiero\s+(un\s+)?recordatorio|"
    r"nuevo\s+recordatorio|"
    r"cre(?:a|á|ar)\s+(un\s+)?recordatorio"
    r")\b"
)


def should_skip_reminder_create(user_text: str) -> bool:
    """True si el usuario solo pregunta/confirma, no pide anotar uno nuevo."""
    text = (user_text or "").strip()
    if not text:
        return False
    if _REMINDER_CREATE_RE.search(text):
        return False
    return _REMINDER_CONFIRM_RE.search(text) is not None


_REMINDER_WHEN_RE = re.compile(
    r"(?i)\b("
    r"pasado\s+ma[nñ]ana|ma[nñ]ana|\bhoy\b|"
    r"a\s+las?\s+\d|"
    r"de\s+la\s+(tarde|noche|ma[nñ]ana)|"
    r"por\s+la\s+(tarde|noche|ma[nñ]ana)|"
    r"el\s+(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)"
    r")\b"
)


def wants_add_reminder(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text or wants_add_task(text) or wants_list_tasks(text):
        return False
    if should_skip_reminder_create(text):
        return False
    return bool(_REMINDER_CREATE_RE.search(text))


def looks_like_timed_reminder(user_text: str) -> bool:
    """Pedido tipo 'comprar vino mañana a las 7 de la tarde' sin decir recordame."""
    text = (user_text or "").strip()
    if not text or wants_add_task(text) or wants_list_tasks(text):
        return False
    if should_skip_reminder_create(text):
        return False
    if _REMINDER_CREATE_RE.search(text):
        return True
    from app.services.recordatory_recurrence import extract_recurrence_from_text

    if extract_recurrence_from_text(text) and _TIME_ONLY_RE.search(text):
        return True
    return bool(_REMINDER_WHEN_RE.search(text)) and (
        bool(_TIME_ONLY_RE.search(text)) or bool(_RELATIVE_DAY_RE.search(text))
    )


def _reminder_display_name(row: Recordatory) -> str:
    message = (row.message or "").strip()
    spec = spec_from_row(
        recurrence=getattr(row, "recurrence", None),
        weekdays=getattr(row, "weekdays", None),
        time_of_day=getattr(row, "time_of_day", None),
    )
    if spec.is_recurring:
        label = describe_recurrence(spec)
        return f"{message} · {label}" if message else label
    if row.scheduled_at is not None:
        when = row.scheduled_at.strftime("%d/%m a las %H:%M")
        return f"{message} · {when}" if message else when
    return message


def _extract_time_for_recurrence(
    *,
    fecha: str | None,
    user_text: str,
    message: str,
) -> tuple[int, int] | None:
    """Hora obligatoria para recurrentes: fecha HH:MM, o reloj en el texto."""
    clock = parse_time_of_day((fecha or "").strip())
    if clock is not None:
        return clock
    # fecha ISO puntual → tomar la hora
    iso = parse_iso_fecha(fecha)
    if iso is not None:
        return iso.hour, iso.minute
    combined = " ".join(p for p in (fecha or "", user_text, message) if p)
    return _extract_clock_from_text(combined)


def ensure_future_datetime(
    dt: datetime | None,
    *,
    user_text: str = "",
    fecha: str = "",
) -> datetime | None:
    """Normaliza fechas sin año explícito al próximo futuro ≤ 365 días.

    - Si el usuario dijo un año (2026, etc.), se respeta tal cual.
    - Si no: se toma mes/día/hora y se ancla al año correcto
      (este o el próximo), sin pasar de 365 días desde ahora.
    - Hora de hoy/ayer ya pasada → +1 día (no +1 año).
    """
    if dt is None:
        return None
    now = _now_local()
    slack = timedelta(minutes=1)
    horizon = now + timedelta(days=365)

    # Año dicho por el usuario → no reinterpretar.
    if _EXPLICIT_YEAR_RE.search(user_text or ""):
        return dt

    # Hora de hoy/ayer ya pasada → sumar días (no años). Evita 15:00 → 2027.
    if dt < now - slack and (now - dt) <= timedelta(days=2):
        candidate = dt
        for _ in range(3):
            if candidate >= now - slack:
                if candidate != dt:
                    logger.info(
                        "Ajustando hora pasada %s → %s (user_text=%r fecha_llm=%r)",
                        dt,
                        candidate,
                        user_text,
                        fecha,
                    )
                return candidate
            candidate = candidate + timedelta(days=1)

    # Sin año: mes/día/hora → próximo instante en [now, now+365d].
    best: datetime | None = None
    for year in (now.year, now.year + 1):
        try:
            candidate = dt.replace(year=year)
        except ValueError:
            try:
                candidate = dt.replace(year=year, day=28)
            except ValueError:
                continue
        if now - slack <= candidate <= horizon:
            if best is None or candidate < best:
                best = candidate

    if best is not None:
        if best != dt:
            logger.info(
                "Ajustando fecha sin año %s → %s (user_text=%r fecha_llm=%r)",
                dt,
                best,
                user_text,
                fecha,
            )
        return best

    return dt


def _dateparser_settings() -> dict[str, Any]:
    return {
        "PREFER_DATES_FROM": "future",
        "PREFER_DAY_OF_MONTH": "first",
        "RELATIVE_BASE": _now_local(),
        "TIMEZONE": "America/Argentina/Buenos_Aires",
        "RETURN_AS_TIMEZONE_AWARE": False,
    }


# hoy / mañana / pasado mañana (+ hora opcional). Evita que el LLM invente ISO incorrectos.
_RELATIVE_DAY_RE = re.compile(
    r"(?P<rel>\bpasado\s+ma[nñ]ana\b|\bma[nñ]ana\b|\bhoy\b)"
    r"(?:\s*(?:a\s+las?\s*)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?)?",
    re.IGNORECASE,
)
# Hora de reloj en ES. "15 horas" NO es duración; "12 de noviembre" no matchea.
_TIME_ONLY_RE = re.compile(
    r"(?:a\s+)?(?:las?\s+)(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?"
    r"|(?<!\d)(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?:horas?|hs|hrs?)\b"
    r"|(?<!\d)(?P<h3>\d{1,2}):(?P<m3>\d{2})\s*(?P<ampm3>am|pm)?"
    r"|(?<!\d)(?P<h4>\d{1,2})\s*(?P<ampm4>am|pm)\b",
    re.IGNORECASE,
)


def _parse_clock(hour_s: str | None, minute_s: str | None, ampm: str | None) -> tuple[int, int] | None:
    if hour_s is None:
        return None
    hour = int(hour_s)
    minute = int(minute_s or 0)
    ampm_l = (ampm or "").lower()
    if ampm_l == "pm" and hour < 12:
        hour += 12
    elif ampm_l == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _extract_clock_from_text(text: str) -> tuple[int, int] | None:
    """Primera hora de reloj en el texto (a las 15 / 15 horas / 15:00 / 3pm)."""
    for tm in _TIME_ONLY_RE.finditer(text or ""):
        g = tm.groupdict()
        h = g.get("h") or g.get("h2") or g.get("h3") or g.get("h4")
        m = g.get("m") or g.get("m2") or g.get("m3")
        ampm = g.get("ampm") or g.get("ampm3") or g.get("ampm4")
        clock = _parse_clock(h, m, ampm)
        if clock:
            hour, minute = _apply_daypart(clock[0], clock[1], text)
            return hour, minute
    return None


def _apply_daypart(hour: int, minute: int, text: str) -> tuple[int, int]:
    """'7 de la tarde' → 19:00. No usa 'mañana' (eso es el día)."""
    if re.search(
        r"(?i)\b(?:de|por)\s+la\s+tarde\b|\bpor\s+la\s+tarde\b",
        text or "",
    ):
        if 1 <= hour <= 11:
            hour += 12
    elif re.search(r"(?i)\b(?:de|por)\s+la\s+noche\b", text or ""):
        if 1 <= hour <= 11:
            hour += 12
        elif hour == 12:
            hour = 0
    return hour, minute


def apply_time_from_text(dt: datetime, text: str) -> datetime:
    """Si el usuario dijo una hora, la aplica sobre dt (sin tocar fecha)."""
    clock = _extract_clock_from_text(text)
    if clock is None:
        return dt
    hour, minute = clock
    return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _datetime_from_clock_only(text: str) -> datetime | None:
    """Si el texto es solo una hora (p.ej. '15 horas'), usa hoy/mañana a esa hora.

    Evita que dateparser trate «15 horas» como duración (±15h → ~03:00).
    """
    text = (text or "").strip()
    if not text:
        return None
    clock = _extract_clock_from_text(text)
    if clock is None:
        return None
    # Debe parecer solo hora, no una fecha completa con día/mes
    if re.search(
        r"\b(\d{1,2}\s+de\s+\w+|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}"
        r"|hoy|ma[nñ]ana|pasado\s+ma[nñ]ana)\b",
        text,
        re.I,
    ):
        return None
    hour, minute = clock
    now = _now_local()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < now - timedelta(minutes=1):
        candidate = candidate + timedelta(days=1)
    return candidate


def parse_relative_spanish(text: str) -> datetime | None:
    """Resuelve hoy/mañana/pasado mañana respecto de ahora (AR)."""
    text = (text or "").strip()
    if not text:
        return None
    match = _RELATIVE_DAY_RE.search(text)
    if not match:
        return None
    rel = re.sub(r"\s+", " ", match.group("rel").lower().replace("ñ", "n"))
    now = _now_local()
    if rel == "hoy":
        day = now.date()
    elif rel == "manana":
        day = (now + timedelta(days=1)).date()
    else:  # pasado manana
        day = (now + timedelta(days=2)).date()

    clock = _parse_clock(match.group("h"), match.group("m"), match.group("ampm"))
    if clock is None:
        clock = _extract_clock_from_text(text)
    if clock is None:
        hour, minute = _DEFAULT_HOUR, _DEFAULT_MINUTE
    else:
        hour, minute = clock
    return datetime(day.year, day.month, day.day, hour, minute)


def parse_iso_fecha(raw: str | None) -> datetime | None:
    """Parsea YYYY-MM-DD HH:MM[:SS] (formato que emite el mini LLM)."""
    text = (raw or "").strip()
    if not text:
        return None
    cleaned = text.replace("T", " ").replace("Z", "").strip()
    if "." in cleaned:
        cleaned = cleaned.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def parse_scheduled_at(raw: str | None) -> datetime | None:
    """Parsea fecha/hora (relativa ES, ISO, dd/mm o dateparser)."""
    text = (raw or "").strip()
    if not text:
        return None

    iso = parse_iso_fecha(text)
    if iso is not None:
        return iso

    relative = parse_relative_spanish(text)
    if relative is not None:
        return relative

    clock_only = _datetime_from_clock_only(text)
    if clock_only is not None:
        return clock_only

    if text.endswith("Z"):
        text = text[:-1]

    for fmt in _DATE_FORMATS:
        try:
            return _apply_default_time(_naive_local(datetime.strptime(text, fmt)))
        except ValueError:
            continue
    try:
        return _apply_default_time(_naive_local(datetime.fromisoformat(text)))
    except ValueError:
        pass

    try:
        import dateparser
    except ImportError:
        logger.warning("dateparser no instalado; no pude parsear %r", raw)
        return None

    parsed = dateparser.parse(text, languages=["es"], settings=_dateparser_settings())
    if parsed is None:
        logger.warning("No pude parsear fecha de recordatorio: %r", raw)
        return None
    parsed_naive = _apply_default_time(_naive_local(parsed))
    # dateparser a veces interpreta «15 horas» como duración; pisar con hora de reloj
    return apply_time_from_text(parsed_naive, raw or "")


def extract_datetime_from_text(text: str) -> tuple[datetime | None, str | None]:
    """Busca una fecha en lenguaje natural. Devuelve (dt, fragmento_encontrado)."""
    text = (text or "").strip()
    if not text:
        return None, None

    relative = parse_relative_spanish(text)
    if relative is not None:
        match = _RELATIVE_DAY_RE.search(text)
        return relative, match.group(0) if match else None

    clock_only = _datetime_from_clock_only(text)
    if clock_only is not None:
        tm = _TIME_ONLY_RE.search(text)
        return clock_only, tm.group(0) if tm else None

    try:
        from dateparser.search import search_dates
    except ImportError:
        return None, None

    hits = search_dates(text, languages=["es"], settings=_dateparser_settings())
    if not hits:
        # Solo hora en una frase larga sin fecha parseable
        clock = _extract_clock_from_text(text)
        if clock is None:
            return None, None
        hour, minute = clock
        now = _now_local()
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < now - timedelta(minutes=1):
            candidate = candidate + timedelta(days=1)
        tm = _TIME_ONLY_RE.search(text)
        return candidate, tm.group(0) if tm else None
    fragment, dt = hits[-1]
    parsed = _apply_default_time(_naive_local(dt))
    parsed = apply_time_from_text(parsed, text)
    return parsed, fragment


def _strip_date_fragment(message: str, fragment: str | None) -> str:
    if not fragment:
        return message.strip()
    cleaned = message.replace(fragment, " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+(el|la|los|las|para|a|de|del)\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^(el|la|los|las|para|a|de|del)\s+", "", cleaned, flags=re.I)
    return cleaned.strip(" .,;:-") or message.strip()


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


def resolve_recordatory_schedule(
    *,
    valor: str,
    fecha: str | None,
    user_text: str = "",
) -> tuple[str, datetime | None]:
    """Resuelve mensaje + fecha. El mini LLM manda YYYY-MM-DD HH:MM; si no, se parsea el texto."""
    message, scheduled_from_valor = _parse_recordatory_valor(valor)
    if not message:
        message = (user_text or "").strip()

    combined = " ".join(p for p in (user_text, fecha or "", message) if p)

    # 1) Fecha absoluta del mini LLM: YYYY-MM-DD HH:MM (del usuario, no del proyecto).
    router_iso = parse_iso_fecha(fecha)
    if router_iso is not None:
        logger.info(
            "Recordatorio: usando fecha ISO del router %s (user_text=%r)",
            router_iso,
            user_text,
        )
        return message.strip(), router_iso

    # 2) Relativos del pedido del usuario (mañana/hoy) si el router no mandó ISO.
    relative = parse_relative_spanish(combined)
    if relative is not None:
        relative = apply_time_from_text(relative, combined)
        relative = ensure_future_datetime(
            relative, user_text=user_text, fecha=fecha or ""
        )
        logger.info(
            "Recordatorio: usando fecha relativa %s (user_text=%r fecha_llm=%r)",
            relative,
            user_text,
            fecha,
        )
        return message.strip(), relative

    # 3) Campo fecha / JSON en valor
    scheduled_at = scheduled_from_valor or parse_scheduled_at(fecha)
    fragment: str | None = None

    # 3) dateparser sobre mensaje / user_text
    if scheduled_at is None:
        scheduled_at, fragment = extract_datetime_from_text(message)
        if scheduled_at is not None:
            message = _strip_date_fragment(message, fragment)
    if scheduled_at is None and user_text:
        scheduled_at, fragment = extract_datetime_from_text(user_text)
        if scheduled_at is not None and message == (user_text or "").strip():
            message = _strip_date_fragment(user_text, fragment)

    # 4) Hora de reloj del usuario pisa duración/ISO mal parseados ("15 horas" ≠ 03:00)
    if scheduled_at is not None:
        scheduled_at = apply_time_from_text(scheduled_at, combined)

    # 5) Corregir año inventado / hora de hoy ya pasada
    scheduled_at = ensure_future_datetime(
        scheduled_at, user_text=user_text, fecha=fecha or ""
    )

    # 6) Si sigue en el pasado / vacía, reintentar con texto del usuario
    if scheduled_at is None or scheduled_at < _now_local() - timedelta(minutes=1):
        retry, fragment = extract_datetime_from_text(user_text or message)
        if retry is not None:
            retry = apply_time_from_text(retry, combined)
        retry = ensure_future_datetime(
            retry, user_text=user_text, fecha=fecha or ""
        )
        if retry is not None and retry >= _now_local() - timedelta(minutes=1):
            logger.info(
                "Recordatorio: descartando fecha %s; uso %s de texto",
                scheduled_at,
                retry,
            )
            scheduled_at = retry

    return message.strip(), scheduled_at

_ES_0_19 = {
    "cero": 0,
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
}
_ES_TENS = {
    "veinte": 20,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}
_ES_VEINTI = {
    "veintiuno": 21,
    "veintiun": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
_DOS_MIL_RE = re.compile(
    r"(?i)\bdos\s+mil(?:\s+y)?(?:\s+"
    r"(?P<tail>veinti[\w]+|veinte(?:\s+y\s+\w+)?|[a-záéíóú]+|\d{1,2})"
    r")?\b"
)


def _strip_accents(text: str) -> str:
    table = str.maketrans("áéíóúü", "aeiouu")
    return (text or "").translate(table)


def _es_number_0_99(text: str) -> int | None:
    raw = _strip_accents(re.sub(r"\s+", " ", (text or "").strip().lower()))
    if not raw:
        return None
    if raw.isdigit():
        value = int(raw)
        return value if 0 <= value <= 99 else None
    if raw in _ES_VEINTI:
        return _ES_VEINTI[raw]
    if raw in _ES_0_19:
        return _ES_0_19[raw]
    if raw in _ES_TENS:
        return _ES_TENS[raw]
    match = re.fullmatch(
        r"(veinte|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa)"
        r"(?:\s+y\s+)?([a-z]+)",
        raw,
    )
    if not match:
        return None
    tens = _ES_TENS.get(match.group(1))
    ones = _ES_0_19.get(match.group(2))
    if tens is None or ones is None or ones >= 10:
        return None
    return tens + ones


def _fold_spoken_years(text: str) -> str:
    """Convierte 'dos mil veintisiete' → '2027' para matchear nombres de proyecto."""

    def repl(match: re.Match[str]) -> str:
        tail = match.group("tail") or ""
        if not tail.strip():
            return "2000"
        units = _es_number_0_99(tail)
        if units is None:
            return match.group(0)
        return str(2000 + units)

    return _DOS_MIL_RE.sub(repl, text or "")


def _normalize_msg(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    return _fold_spoken_years(cleaned)


def _message_match_score(query: str, message: str) -> float:
    q = _normalize_msg(query)
    m = _normalize_msg(message)
    if not q or not m:
        return 0.0
    if q == m:
        return 1.0
    if q in m or m in q:
        return 0.85
    q_tokens = {t for t in re.split(r"\W+", q) if len(t) > 2}
    m_tokens = {t for t in re.split(r"\W+", m) if len(t) > 2}
    if not q_tokens:
        return 0.0
    overlap = len(q_tokens & m_tokens) / len(q_tokens)
    return overlap if overlap >= 0.5 else 0.0


def _slot_close(
    target: datetime,
    candidate: datetime | None,
    *,
    minutes: int = 90,
) -> bool:
    """Compara mes/día/hora permitiendo desfase de año (recordatorios ya cargados)."""
    if candidate is None:
        return False
    target = _naive_local(target)
    candidate = _naive_local(candidate)
    years = {target.year, candidate.year, _now_local().year, _now_local().year + 1}
    for year in years:
        try:
            t = target.replace(year=year)
        except ValueError:
            try:
                t = target.replace(year=year, day=28)
            except ValueError:
                continue
        if abs((t - candidate).total_seconds()) <= minutes * 60:
            return True
    return abs((target - candidate).total_seconds()) <= minutes * 60


def _format_when(dt: datetime | None) -> str:
    if dt is None:
        return "sin fecha"
    return _naive_local(dt).strftime("%d/%m a las %H:%M")


def find_recordatories_for_delete(
    repo: RecordatoryRepository,
    *,
    user_id: int,
    message: str,
    scheduled_at: datetime | None,
) -> list[Recordatory]:
    """Busca candidatos activos por mensaje (+ fecha si hay)."""
    rows = repo.list_for_user(user_id, limit=50)
    scored: list[tuple[float, Recordatory]] = []
    for row in rows:
        score = _message_match_score(message, row.message)
        if score <= 0:
            continue
        if scheduled_at is not None and not _slot_close(scheduled_at, row.scheduled_at):
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [row for _, row in scored]


def find_tasks_for_delete(
    repo: TasksRepository,
    *,
    user_id: int,
    proyect_id: int,
    name: str,
) -> list[Tasks]:
    """Busca tareas activas del proyecto por nombre."""
    rows = repo.list_for_project(user_id, proyect_id)
    scored: list[tuple[float, Tasks]] = []
    for row in rows:
        score = _message_match_score(name, row.name or "")
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    if not scored:
        return []
    best = scored[0][0]
    return [row for score, row in scored if score >= best - 0.01]


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
            "agregar_proyecto": self._agregar_proyecto,
            "seleccionar_proyecto": self._seleccionar_proyecto,
            "seleccionar_proyectos": self._seleccionar_proyecto,
            "eliminar_proyecto": self._eliminar_proyecto,
            "eliminar_proyectos": self._eliminar_proyecto,
            "agregar_tarea": self._agregar_tarea,
            "eliminar_tarea": self._eliminar_tarea,
            "eliminar_tareas": self._eliminar_tarea,
            "eliminar_mensajes": self._eliminar_mensajes,
            "eliminar_mensaje": self._eliminar_mensajes,
            "listar_recordatorios": self._listar_recordatorios,
            "listar_recordatorio": self._listar_recordatorios,
            "listar_proyectos": self._listar_proyectos,
            "listar_tareas": self._listar_tareas,
            "eliminar_recordatorio": self._eliminar_recordatorio,
            "eliminar_recordatorios": self._eliminar_recordatorio,
        }

    async def dispatch(
        self,
        *,
        clave: str,
        valor: str,
        user: Optional[User],
        user_text: str = "",
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
        recurrencia: str | None = None,
        dias: str | None = None,
    ) -> ActionResult | None:
        row = self.repo.get_by_clave(clave)
        metodo = (row.metodo or "").strip() if row is not None else clave.strip().lower()
        handler = self._handlers.get(metodo)
        if row is None and handler is None:
            logger.warning("Acción desconocida clave=%r (no está en tabla actions)", clave)
            return None
        if handler is None:
            logger.error(
                "Método %r no registrado para clave=%r (actions.id=%s)",
                metodo,
                clave,
                getattr(row, "id", None),
            )
            return ActionResult(
                action=clave,
                metodo=metodo,
                valor=valor,
                extra={"error": "handler_not_registered"},
            )

        logger.info(
            "Dispatch action clave=%s metodo=%s valor=%r valor1=%r fecha=%r "
            "recurrencia=%r dias=%r confirm=%r",
            clave,
            metodo,
            valor,
            valor1,
            fecha,
            recurrencia,
            dias,
            confirm,
        )
        kwargs: dict[str, Any] = {
            "valor": valor,
            "user": user,
            "user_text": user_text,
            "row_clave": clave,
            "fecha": fecha,
            "confirm": confirm,
        }
        signature = inspect.signature(handler).parameters
        if "valor1" in signature:
            kwargs["valor1"] = valor1 or ""
        if "recurrencia" in signature:
            kwargs["recurrencia"] = recurrencia or ""
        if "dias" in signature:
            kwargs["dias"] = dias or ""
        return await handler(**kwargs)

    async def _ver_video(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
    ) -> ActionResult:
        yt = YouTubeService(self.settings)
        result = await yt.play(
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
        confirm: bool | None = None,
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
        confirm: bool | None = None,
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
        confirm: bool | None = None,
        recurrencia: str | None = None,
        dias: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=valor,
                spoken_override="No puedo guardar el recordatorio sin saber quién sos.",
                extra={"error": "no_user"},
            )

        # El LLM a veces reemite el JSON cuando preguntan "¿lo anotaste?".
        if should_skip_reminder_create(user_text):
            logger.info(
                "Recordatorio: skip create (confirmación) user_text=%r",
                user_text[:120],
            )
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=valor,
                spoken_override="Sí, ya lo tengo anotado.",
                extra={"skipped": "confirmation_turn"},
            )

        uid = int(user.id)
        pending = _pending_add_reminder.get(uid) or {}
        router_rec = (recurrencia or pending.get("recurrencia") or "").strip()
        router_dias = (dias or pending.get("dias") or "").strip()

        message, scheduled_at = resolve_recordatory_schedule(
            valor=valor or pending.get("message") or "",
            fecha=fecha,
            user_text=user_text,
        )
        if not message and pending.get("message"):
            message = pending["message"]
        message = strip_recurrence_fragment(message)
        if not message:
            _pending_add_reminder[uid] = {
                "message": "",
                "recurrencia": router_rec,
                "dias": router_dias,
            }
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=valor,
                spoken_override="No entendí qué tengo que recordarte.",
                extra={"error": "empty_message"},
            )

        spec = resolve_recurrence(
            user_text=user_text,
            message=f"{valor} {message}",
            router_recurrence=router_rec or None,
            router_days=router_dias or None,
        )

        # Recurrente: hora obligatoria; no exige día absoluto.
        if spec.is_recurring:
            clock = _extract_time_for_recurrence(
                fecha=fecha,
                user_text=user_text,
                message=message,
            )
            if clock is None:
                _pending_add_reminder[uid] = {
                    "message": message,
                    "recurrencia": spec.recurrence,
                    "dias": spec.weekdays or "",
                }
                return ActionResult(
                    action=row_clave,
                    metodo="agregar_recordatorio",
                    valor=message,
                    spoken_override="Decime a qué hora y lo anoto.",
                    extra={
                        "error": "missing_hora",
                        "message": message,
                        "recurrence": spec.recurrence,
                        "weekdays": spec.weekdays,
                    },
                )
            hour, minute = clock
            time_of_day = normalize_time_of_day(hour, minute)
            spec = RecurrenceSpec(
                recurrence=spec.recurrence,
                weekdays=spec.weekdays,
                time_of_day=time_of_day,
            )
            scheduled_at = next_occurrence(spec, after=_now_local(), inclusive=True)
            if scheduled_at is None:
                return ActionResult(
                    action=row_clave,
                    metodo="agregar_recordatorio",
                    valor=message,
                    spoken_override="No pude calcular la próxima fecha. Probá de nuevo.",
                    extra={"error": "schedule_failed", "message": message},
                )

            repo = RecordatoryRepository(self.db)
            existing = repo.find_active_duplicate(
                user_id=uid,
                message=message,
                scheduled_at=scheduled_at,
                recurrence=spec.recurrence,
                time_of_day=time_of_day,
                weekdays=spec.weekdays,
            )
            if existing is not None:
                _pending_add_reminder.pop(uid, None)
                spoken = (
                    f"Sí, ya lo tenía anotado: {describe_recurrence(spec)}."
                )
                return ActionResult(
                    action=row_clave,
                    metodo="agregar_recordatorio",
                    valor=message,
                    spoken_override=spoken,
                    extra={
                        "id": existing.id,
                        "message": existing.message,
                        "scheduled_at": (
                            existing.scheduled_at.isoformat(sep=" ")
                            if existing.scheduled_at
                            else None
                        ),
                        "recurrence": spec.recurrence,
                        "weekdays": spec.weekdays,
                        "time_of_day": time_of_day,
                        "deduped": True,
                    },
                )

            row = repo.create(
                user_id=uid,
                message=message,
                scheduled_at=scheduled_at,
                recurrence=spec.recurrence,
                time_of_day=time_of_day,
                weekdays=spec.weekdays,
            )
            _pending_add_reminder.pop(uid, None)
            label = describe_recurrence(spec)
            logger.info(
                "Recordatorio recurrente id=%s user=%s recurrence=%s time=%s "
                "weekdays=%s next=%s message=%r",
                row.id,
                user.id,
                spec.recurrence,
                time_of_day,
                spec.weekdays,
                scheduled_at,
                message[:80],
            )
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=message,
                spoken_override=f"Listo, te lo recuerdo {label}.",
                extra={
                    "id": row.id,
                    "message": row.message,
                    "scheduled_at": scheduled_at.isoformat(sep=" "),
                    "recurrence": spec.recurrence,
                    "weekdays": spec.weekdays,
                    "time_of_day": time_of_day,
                },
            )

        # Puntual (once): día + hora
        if scheduled_at is None:
            _pending_add_reminder[uid] = {
                "message": message,
                "recurrencia": RECURRENCE_ONCE,
            }
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=message,
                spoken_override="Decime día y hora y lo anoto.",
                extra={"error": "missing_fecha", "message": message},
            )

        now = datetime.now(_TZ).replace(tzinfo=None)
        if scheduled_at < now - timedelta(minutes=1):
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=message,
                spoken_override="Esa fecha ya pasó. Decime un día y hora futuros.",
                extra={
                    "error": "past_fecha",
                    "message": message,
                    "scheduled_at": scheduled_at.isoformat(sep=" "),
                },
            )

        time_of_day = normalize_time_of_day(scheduled_at.hour, scheduled_at.minute)
        repo = RecordatoryRepository(self.db)
        existing = repo.find_active_duplicate(
            user_id=uid,
            message=message,
            scheduled_at=scheduled_at,
            recurrence=RECURRENCE_ONCE,
            time_of_day=time_of_day,
        )
        if existing is not None:
            logger.info(
                "Recordatorio duplicado id=%s user=%s scheduled_at=%s message=%r",
                existing.id,
                user.id,
                scheduled_at,
                message[:80],
            )
            spoken = (
                f"Sí, ya lo tenía anotado para el "
                f"{scheduled_at.strftime('%d/%m a las %H:%M')}."
            )
            _pending_add_reminder.pop(uid, None)
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=message,
                spoken_override=spoken,
                extra={
                    "id": existing.id,
                    "message": existing.message,
                    "scheduled_at": scheduled_at.isoformat(sep=" "),
                    "recurrence": RECURRENCE_ONCE,
                    "deduped": True,
                },
            )

        row = repo.create(
            user_id=uid,
            message=message,
            scheduled_at=scheduled_at,
            recurrence=RECURRENCE_ONCE,
            time_of_day=time_of_day,
        )
        _pending_add_reminder.pop(uid, None)
        logger.info(
            "Recordatorio id=%s user=%s scheduled_at=%s message=%r",
            row.id,
            user.id,
            scheduled_at,
            message[:80],
        )
        spoken = f"Listo, te lo recuerdo el {scheduled_at.strftime('%d/%m a las %H:%M')}."
        return ActionResult(
            action=row_clave,
            metodo="agregar_recordatorio",
            valor=message,
            spoken_override=spoken,
            extra={
                "id": row.id,
                "message": row.message,
                "scheduled_at": scheduled_at.isoformat(sep=" "),
                "recurrence": RECURRENCE_ONCE,
                "time_of_day": time_of_day,
            },
        )

    async def _seleccionar_proyecto(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
    ) -> ActionResult:
        from app.bot.proyect_context import set_selected_proyect_id

        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="seleccionar_proyecto",
                valor=valor,
                spoken_override="No puedo cambiar de proyecto sin saber quién sos.",
                extra={"error": "no_user"},
            )

        user_id = int(user.id)
        name = (
            _clean_project_name(valor)
            or extract_select_project_name(user_text)
            or _clean_project_name(valor1 or "")
        )
        if not name:
            return ActionResult(
                action=row_clave,
                metodo="seleccionar_proyecto",
                valor=valor,
                spoken_override="¿Qué proyecto querés seleccionar?",
                extra={"error": "missing_name", "needs_project": True},
            )

        matches = self._resolve_project_ref(user_id, name)
        if not matches:
            projects = ProyectsRepository(self.db).list_for_user(user_id)
            matches = _match_projects(projects, name)

        if not matches:
            return ActionResult(
                action=row_clave,
                metodo="seleccionar_proyecto",
                valor=name,
                spoken_override=f"No encontré un proyecto que se llame {name}.",
                extra={"error": "not_found", "items": []},
            )

        if len(matches) > 1:
            items = [
                {"id": row.id, "name": row.name}
                for row in matches
                if (row.name or "").strip()
            ]
            spoken = (
                "Encontré varios proyectos parecidos. "
                "¿Cuál querés: "
                + ", ".join(item["name"] for item in items)
                + "?"
            )
            display = "Proyectos:\n" + "\n".join(f"• {item['name']}" for item in items)
            return ActionResult(
                action=row_clave,
                metodo="seleccionar_proyecto",
                valor=name,
                spoken_override=spoken,
                extra={
                    "error": "ambiguous",
                    "needs_project": True,
                    "items": items,
                    "display": display,
                    "items_title": "Elegí un proyecto",
                },
            )

        row = matches[0]
        set_selected_proyect_id(int(row.id))
        logger.info(
            "Proyecto seleccionado id=%s user=%s name=%r",
            row.id,
            user.id,
            row.name,
        )
        spoken = f"Listo, seleccioné el proyecto {row.name}."
        return ActionResult(
            action=row_clave,
            metodo="seleccionar_proyecto",
            valor=row.name,
            spoken_override=spoken,
            extra={
                "proyect_id": int(row.id),
                "id": int(row.id),
                "name": row.name,
                "selected": True,
                "ui_select": True,
            },
        )

    async def _agregar_proyecto(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="agregar_proyecto",
                valor=valor,
                spoken_override="No puedo crear el proyecto sin saber quién sos.",
                extra={"error": "no_user"},
            )

        name = _clean_project_name(valor) or extract_add_project_name(user_text)
        if not name:
            return ActionResult(
                action=row_clave,
                metodo="agregar_proyecto",
                valor=valor,
                spoken_override="¿Cómo se llama el proyecto?",
                extra={"error": "missing_name"},
            )

        repo = ProyectsRepository(self.db)
        existing = repo.find_active_duplicate(user_id=int(user.id), name=name)
        if existing is not None:
            logger.info(
                "Proyecto duplicado id=%s user=%s name=%r",
                existing.id,
                user.id,
                existing.name,
            )
            spoken = f"Ya tenés un proyecto que se llama {existing.name}."
            return ActionResult(
                action=row_clave,
                metodo="agregar_proyecto",
                valor=name,
                spoken_override=spoken,
                extra={
                    "id": existing.id,
                    "name": existing.name,
                    "proyect_id": existing.id,
                    "deduped": True,
                },
            )

        row = repo.create(user_id=int(user.id), name=name)
        logger.info("Proyecto id=%s user=%s name=%r", row.id, user.id, row.name)
        spoken = f"Listo, creé el proyecto {row.name}."
        return ActionResult(
            action=row_clave,
            metodo="agregar_proyecto",
            valor=name,
            spoken_override=spoken,
            extra={"id": row.id, "name": row.name, "proyect_id": row.id},
        )

    async def _eliminar_proyecto(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=valor,
                spoken_override="No puedo borrar el proyecto sin saber quién sos.",
                extra={"error": "no_user"},
            )

        user_id = int(user.id)
        text = (user_text or "").strip()
        pending = _pending_delete_project.get(user_id) or {}
        repo = ProyectsRepository(self.db)
        projects = [
            row
            for row in repo.list_for_user(user_id)
            if not is_default_project_name(row.name)
        ]
        name = (
            _clean_project_name(valor)
            or extract_delete_project_name(user_text)
            or str(pending.get("name") or "").strip()
        )

        def remember(**fields: Any) -> None:
            current = _pending_delete_project.get(user_id) or {}
            current.update(fields)
            _pending_delete_project[user_id] = current

        def ask_which(
            choices: list[Proyects],
            spoken: str,
            display: str,
            title: str,
        ) -> ActionResult:
            remember(name=name, proyect_id=None)
            items = [
                {"id": row.id, "name": row.name}
                for row in choices
                if (row.name or "").strip()
            ]
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=name,
                spoken_override=spoken,
                extra={
                    "count": len(items),
                    "items": items,
                    "display": display,
                    "items_title": title,
                    "needs_project": True,
                },
            )

        def refuse_default() -> ActionResult:
            _pending_delete_project.pop(user_id, None)
            spoken = f"El proyecto {DEFAULT_PROJECT_NAME} no se puede borrar."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=DEFAULT_PROJECT_NAME,
                spoken_override=spoken,
                extra={"error": "protected_project", "display": spoken},
            )

        def warn(row: Proyects) -> ActionResult:
            if is_default_project_name(row.name):
                return refuse_default()
            tasks_n, messages_n = repo.count_related(int(row.id))
            remember(name=row.name, proyect_id=int(row.id))
            spoken = (
                f"Si borro el proyecto {row.name}, también se van a borrar las tareas "
                f"y las conversaciones asociadas. Ahora tiene {tasks_n} "
                f"{'tarea' if tasks_n == 1 else 'tareas'} y {messages_n} "
                f"{'mensaje' if messages_n == 1 else 'mensajes'}. "
                "El borrado es definitivo. ¿Confirmás?"
            )
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=row.name,
                spoken_override=spoken,
                extra={
                    "needs_confirm": True,
                    "id": row.id,
                    "name": row.name,
                    "proyect_id": row.id,
                    "tasks": tasks_n,
                    "messages": messages_n,
                    "display": spoken,
                },
            )

        if user_id in _pending_delete_project and (
            _DENY_RE.search(text) or confirm is False
        ):
            _pending_delete_project.pop(user_id, None)
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=valor,
                spoken_override="Listo, no borro nada.",
                extra={"cancelled": True},
            )

        wants_confirm = bool(confirm) or bool(_AFFIRM_RE.search(text))
        pending_id = pending.get("proyect_id")
        if wants_confirm and pending_id is not None:
            row = repo.get_active_for_user(user_id, int(pending_id))
            if row is None:
                _pending_delete_project.pop(user_id, None)
                return ActionResult(
                    action=row_clave,
                    metodo="eliminar_proyecto",
                    valor=valor,
                    spoken_override="Ese proyecto ya no está. ¿Cuál querés borrar?",
                    extra={"error": "pending_missing"},
                )
            if is_default_project_name(row.name):
                return refuse_default()
            deleted = repo.delete(row)
            default = repo.ensure_default_for_user(user_id)
            _pending_delete_project.pop(user_id, None)
            spoken = (
                f"Listo, borré el proyecto {deleted['name']}, "
                "con sus tareas y conversaciones. "
                f"Seguimos en {default.name}."
            )
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=str(deleted["name"]),
                spoken_override=spoken,
                extra={
                    "deleted": True,
                    "id": deleted["id"],
                    "name": deleted["name"],
                    "tasks": deleted["tasks"],
                    "messages": deleted["messages"],
                    "proyect_id": default.id,
                    "proyect_name": default.name,
                },
            )

        if is_default_project_name(name):
            return refuse_default()

        if not projects:
            _pending_delete_project.pop(user_id, None)
            spoken = (
                f"No tenés otros proyectos para borrar. "
                f"{DEFAULT_PROJECT_NAME} no se puede eliminar."
            )
            return ActionResult(
                action=row_clave,
                metodo="eliminar_proyecto",
                valor=name,
                spoken_override=spoken,
                extra={"error": "no_projects", "display": spoken},
            )

        if not name:
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_which(
                projects,
                f"¿Cuál proyecto borro? Tené en cuenta que también se borran "
                f"las tareas y las conversaciones asociadas. Tenés: {names}.",
                "¿Cuál proyecto borro? También se borran tareas y conversaciones.",
                "¿Cuál proyecto?",
            )

        matches = self._resolve_project_ref(user_id, name)
        if not matches:
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_which(
                projects,
                f"No encontré un proyecto que se llame {name}. "
                f"¿Cuál borro? {names}.",
                f"No encontré {name}. ¿Cuál proyecto borro?",
                "Tus proyectos",
            )

        if len(matches) > 1:
            names = ", ".join(row.name for row in matches if (row.name or "").strip())
            return ask_which(
                matches,
                f"Encontré más de un proyecto. ¿Cuál borro? {names}.",
                "Encontré más de un proyecto. ¿Cuál borro?",
                "¿Cuál de estos?",
            )

        return warn(matches[0])

    def _resolve_project_ref(
        self, user_id: int, valor1: str
    ) -> list[Proyects]:
        token = (valor1 or "").strip()
        if not token:
            return []
        repo = ProyectsRepository(self.db)
        if token.isdigit():
            num = int(token)
            # Un año (2027) no es un id de proyecto.
            if not (1900 <= num <= 2100):
                row = repo.get_active_for_user(user_id, num)
                if row is not None:
                    return [row]
        projects = repo.list_for_user(user_id)
        return _match_projects(projects, token)

    async def _agregar_tarea(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="agregar_tarea",
                valor=valor,
                spoken_override="No puedo crear la tarea sin saber quién sos.",
                extra={"error": "no_user"},
            )

        user_id = int(user.id)
        ext_name, _ext_proj = extract_add_task_parts(user_text)
        name = _clean_task_name(valor) or ext_name
        proyect_ref = (valor1 or "").strip()

        projects = ProyectsRepository(self.db).list_for_user(user_id)

        def remember(**fields: str) -> None:
            current = _pending_add_task.get(user_id) or {}
            current.update(fields)
            _pending_add_task[user_id] = current

        def ask_project(
            choices: list[Proyects],
            spoken: str,
            display: str,
            title: str,
        ) -> ActionResult:
            remember(name=name, proyect_ref="")
            items = [
                {"id": row.id, "name": row.name}
                for row in choices
                if (row.name or "").strip()
            ]
            return ActionResult(
                action=row_clave,
                metodo="agregar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={
                    "count": len(items),
                    "items": items,
                    "display": display,
                    "items_title": title,
                    "needs_project": True,
                    "name": name,
                },
            )

        if not name:
            remember(name="", proyect_ref=proyect_ref)
            spoken = "¿Cómo se llama la tarea?"
            return ActionResult(
                action=row_clave,
                metodo="agregar_tarea",
                valor=valor,
                spoken_override=spoken,
                extra={
                    "error": "missing_name",
                    "display": spoken,
                    "proyect_ref": proyect_ref,
                },
            )

        if not projects:
            _pending_add_task.pop(user_id, None)
            spoken = "No tenés proyectos todavía. Primero creá un proyecto para agregarle tareas."
            return ActionResult(
                action=row_clave,
                metodo="agregar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={"error": "no_projects", "display": spoken, "name": name},
            )

        if not proyect_ref:
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_project(
                projects,
                f"¿A qué proyecto agrego la tarea {name}? Tenés: {names}.",
                f"¿A qué proyecto agrego «{name}»?",
                "¿A qué proyecto?",
            )

        matches = self._resolve_project_ref(user_id, proyect_ref)
        if not matches:
            if proyect_ref.isdigit():
                _pending_add_task.pop(user_id, None)
                spoken = "No encontré el proyecto seleccionado para agregar la tarea."
                return ActionResult(
                    action=row_clave,
                    metodo="agregar_tarea",
                    valor=name,
                    spoken_override=spoken,
                    extra={"error": "project_not_found", "display": spoken, "name": name},
                )
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_project(
                projects,
                f"No encontré un proyecto que se llame {proyect_ref}. "
                f"¿A cuál agrego la tarea {name}? {names}.",
                f"No encontré {proyect_ref}. ¿A qué proyecto agrego {name}?",
                "Tus proyectos",
            )

        if len(matches) > 1:
            names = ", ".join(row.name for row in matches if (row.name or "").strip())
            return ask_project(
                matches,
                f"Encontré más de un proyecto. ¿A cuál agrego {name}? {names}.",
                f"¿A cuál de estos proyectos agrego {name}?",
                "¿Cuál de estos?",
            )

        proyect = matches[0]
        repo = TasksRepository(self.db)
        existing = repo.find_active_duplicate(proyect_id=int(proyect.id), name=name)
        if existing is not None:
            _pending_add_task.pop(user_id, None)
            spoken = f"Ya tenés la tarea {existing.name} en {proyect.name}."
            return ActionResult(
                action=row_clave,
                metodo="agregar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={
                    "id": existing.id,
                    "name": existing.name,
                    "proyect_id": proyect.id,
                    "proyect_name": proyect.name,
                    "deduped": True,
                },
            )

        row = repo.create(proyect_id=int(proyect.id), name=name)
        _pending_add_task.pop(user_id, None)
        logger.info(
            "Tarea id=%s proyect=%s user=%s name=%r",
            row.id,
            proyect.id,
            user.id,
            row.name,
        )
        spoken = f"Listo, agregué {row.name} al proyecto {proyect.name}."
        return ActionResult(
            action=row_clave,
            metodo="agregar_tarea",
            valor=name,
            spoken_override=spoken,
            extra={
                "id": row.id,
                "name": row.name,
                "proyect_id": proyect.id,
                "proyect_name": proyect.name,
                "valor1": str(proyect.id),
            },
        )

    async def _eliminar_tarea(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=valor,
                spoken_override="No puedo borrar la tarea sin saber quién sos.",
                extra={"error": "no_user"},
            )

        user_id = int(user.id)
        text = (user_text or "").strip()
        pending = _pending_delete_task.get(user_id) or {}
        ext_name = extract_delete_task_name(user_text)
        name = _clean_task_name(valor) or ext_name or str(pending.get("name") or "").strip()
        proyect_ref = (
            (valor1 or "").strip()
            or str(pending.get("proyect_ref") or "").strip()
        )
        repo = TasksRepository(self.db)
        projects = ProyectsRepository(self.db).list_for_user(user_id)

        def remember(**fields: Any) -> None:
            current = _pending_delete_task.get(user_id) or {}
            current.update(fields)
            _pending_delete_task[user_id] = current

        def ask_project(
            choices: list[Proyects],
            spoken: str,
            display: str,
            title: str,
        ) -> ActionResult:
            remember(name=name, proyect_ref="", task_id=None)
            items = [
                {"id": row.id, "name": row.name}
                for row in choices
                if (row.name or "").strip()
            ]
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={
                    "count": len(items),
                    "items": items,
                    "display": display,
                    "items_title": title,
                    "needs_project": True,
                    "name": name,
                },
            )

        if user_id in _pending_delete_task and (
            _DENY_RE.search(text) or confirm is False
        ):
            _pending_delete_task.pop(user_id, None)
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=valor,
                spoken_override="Listo, no borro nada.",
                extra={"cancelled": True},
            )

        wants_confirm = bool(confirm) or bool(_AFFIRM_RE.search(text))
        pending_id = pending.get("task_id")
        pending_project = str(pending.get("proyect_ref") or "").strip()
        if wants_confirm and pending_id is not None and pending_project.isdigit():
            row = repo.get_active_for_project(
                user_id, int(pending_project), int(pending_id)
            )
            if row is None:
                _pending_delete_task.pop(user_id, None)
                return ActionResult(
                    action=row_clave,
                    metodo="eliminar_tarea",
                    valor=valor,
                    spoken_override="Esa tarea ya no está. ¿Cuál querés borrar?",
                    extra={"error": "pending_missing"},
                )
            proyect = self._resolve_project_ref(user_id, pending_project)
            proyect_name = proyect[0].name if proyect else ""
            deleted_name = row.name
            deleted_id = row.id
            repo.delete(row)
            _pending_delete_task.pop(user_id, None)
            spoken = f"Listo, borré {deleted_name} del proyecto {proyect_name}."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=deleted_name,
                spoken_override=spoken,
                extra={
                    "deleted": True,
                    "id": deleted_id,
                    "name": deleted_name,
                    "proyect_id": int(pending_project),
                    "proyect_name": proyect_name,
                },
            )

        if not projects:
            _pending_delete_task.pop(user_id, None)
            spoken = "No tenés proyectos todavía. Primero creá un proyecto para borrar tareas."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={"error": "no_projects", "display": spoken, "name": name},
            )

        if not name:
            remember(name="", proyect_ref=proyect_ref, task_id=None)
            spoken = "¿Cuál tarea borro?"
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=valor,
                spoken_override=spoken,
                extra={
                    "error": "missing_name",
                    "display": spoken,
                    "proyect_ref": proyect_ref,
                },
            )

        if not proyect_ref:
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_project(
                projects,
                f"¿De qué proyecto borro la tarea {name}? Tenés: {names}.",
                f"¿De qué proyecto borro «{name}»?",
                "¿De qué proyecto?",
            )

        matches = self._resolve_project_ref(user_id, proyect_ref)
        if not matches:
            if proyect_ref.isdigit():
                _pending_delete_task.pop(user_id, None)
                spoken = "No encontré el proyecto seleccionado para borrar la tarea."
                return ActionResult(
                    action=row_clave,
                    metodo="eliminar_tarea",
                    valor=name,
                    spoken_override=spoken,
                    extra={"error": "project_not_found", "display": spoken, "name": name},
                )
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_project(
                projects,
                f"No encontré un proyecto que se llame {proyect_ref}. "
                f"¿De cuál borro la tarea {name}? {names}.",
                f"No encontré {proyect_ref}. ¿De qué proyecto borro «{name}»?",
                "Tus proyectos",
            )

        if len(matches) > 1:
            names = ", ".join(row.name for row in matches if (row.name or "").strip())
            return ask_project(
                matches,
                f"Encontré más de un proyecto. ¿De cuál borro {name}? {names}.",
                f"¿De cuál de estos proyectos borro «{name}»?",
                "¿Cuál de estos?",
            )

        proyect = matches[0]
        task_matches = find_tasks_for_delete(
            repo,
            user_id=user_id,
            proyect_id=int(proyect.id),
            name=name,
        )
        if not task_matches:
            remember(name="", proyect_ref=str(proyect.id), task_id=None)
            spoken = (
                f"No encontré la tarea {name} en {proyect.name}. "
                "¿Cuál querés borrar?"
            )
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={
                    "error": "not_found",
                    "display": spoken,
                    "name": name,
                    "proyect_id": proyect.id,
                    "proyect_name": proyect.name,
                },
            )

        if len(task_matches) > 1:
            labels = ", ".join(
                row.name for row in task_matches if (row.name or "").strip()
            )
            remember(name=name, proyect_ref=str(proyect.id), task_id=None)
            spoken = f"Encontré más de una. ¿Cuál borro? {labels}."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=name,
                spoken_override=spoken,
                extra={
                    "error": "ambiguous",
                    "items": [
                        {"id": row.id, "name": row.name} for row in task_matches[:5]
                    ],
                    "display": spoken,
                    "items_title": f"Tareas de {proyect.name}",
                    "proyect_id": proyect.id,
                    "proyect_name": proyect.name,
                },
            )

        row = task_matches[0]
        if wants_confirm or confirm is True:
            deleted_name = row.name
            deleted_id = row.id
            repo.delete(row)
            _pending_delete_task.pop(user_id, None)
            spoken = f"Listo, borré {deleted_name} del proyecto {proyect.name}."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_tarea",
                valor=deleted_name,
                spoken_override=spoken,
                extra={
                    "deleted": True,
                    "id": deleted_id,
                    "name": deleted_name,
                    "proyect_id": proyect.id,
                    "proyect_name": proyect.name,
                },
            )

        remember(name=row.name, proyect_ref=str(proyect.id), task_id=int(row.id))
        spoken = f"Encontré {row.name} en {proyect.name}. ¿Confirmás que la borro?"
        return ActionResult(
            action=row_clave,
            metodo="eliminar_tarea",
            valor=row.name,
            spoken_override=spoken,
            extra={
                "needs_confirm": True,
                "id": row.id,
                "name": row.name,
                "proyect_id": proyect.id,
                "proyect_name": proyect.name,
            },
        )

    async def _eliminar_mensajes(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="eliminar_mensajes",
                valor=valor,
                spoken_override="No puedo borrar los mensajes sin saber quién sos.",
                extra={"error": "no_user"},
            )

        user_id = int(user.id)
        text = (user_text or "").strip()
        pending = _pending_delete_messages.get(user_id) or {}
        ref = (valor1 or "").strip() or str(pending.get("proyect_id") or "").strip()
        messages = ConversationRepository(self.db)

        def remember(**fields: Any) -> None:
            current = _pending_delete_messages.get(user_id) or {}
            current.update(fields)
            _pending_delete_messages[user_id] = current

        if user_id in _pending_delete_messages and (
            _DENY_RE.search(text) or confirm is False
        ):
            _pending_delete_messages.pop(user_id, None)
            return ActionResult(
                action=row_clave,
                metodo="eliminar_mensajes",
                valor=valor,
                spoken_override="Listo, no borro nada.",
                extra={"cancelled": True},
            )

        matches = self._resolve_project_ref(user_id, ref) if ref else []
        if not matches:
            _pending_delete_messages.pop(user_id, None)
            spoken = "No hay un proyecto seleccionado para borrar los mensajes."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_mensajes",
                valor=valor,
                spoken_override=spoken,
                extra={"error": "no_project", "display": spoken},
            )

        proyect = matches[0]
        count = messages.count_for_user_proyect(user_id, int(proyect.id))
        wants_confirm = bool(confirm) or bool(_AFFIRM_RE.search(text))
        pending_id = pending.get("proyect_id")

        if wants_confirm and pending_id is not None:
            row = ProyectsRepository(self.db).get_active_for_user(
                user_id, int(pending_id)
            )
            if row is None:
                _pending_delete_messages.pop(user_id, None)
                spoken = "Ese proyecto ya no está. No borré mensajes."
                return ActionResult(
                    action=row_clave,
                    metodo="eliminar_mensajes",
                    valor=valor,
                    spoken_override=spoken,
                    extra={"error": "pending_missing", "display": spoken},
                )
            deleted = messages.delete_for_user_proyect(user_id, int(row.id))
            _pending_delete_messages.pop(user_id, None)
            if deleted <= 0:
                spoken = f"En {row.name} no había mensajes para borrar."
            elif deleted == 1:
                spoken = f"Listo, borré el mensaje de {row.name}."
            else:
                spoken = f"Listo, borré los {deleted} mensajes de {row.name}."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_mensajes",
                valor=str(row.name),
                spoken_override=spoken,
                extra={
                    "deleted": True,
                    "count": deleted,
                    "proyect_id": row.id,
                    "proyect_name": row.name,
                    "reload_chat": True,
                    "display": spoken,
                },
            )

        if count <= 0:
            _pending_delete_messages.pop(user_id, None)
            spoken = f"En {proyect.name} no hay mensajes para borrar."
            return ActionResult(
                action=row_clave,
                metodo="eliminar_mensajes",
                valor=proyect.name,
                spoken_override=spoken,
                extra={
                    "count": 0,
                    "proyect_id": proyect.id,
                    "proyect_name": proyect.name,
                    "display": spoken,
                },
            )

        remember(proyect_id=int(proyect.id))
        noun = "mensaje" if count == 1 else "mensajes"
        spoken = (
            f"Voy a borrar {count} {noun} de {proyect.name}. "
            "El borrado es definitivo. ¿Confirmás?"
        )
        return ActionResult(
            action=row_clave,
            metodo="eliminar_mensajes",
            valor=proyect.name,
            spoken_override=spoken,
            extra={
                "needs_confirm": True,
                "count": count,
                "proyect_id": proyect.id,
                "proyect_name": proyect.name,
                "display": spoken,
            },
        )

    async def _eliminar_recordatorio(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=valor,
                spoken_override="No puedo borrar recordatorios sin saber quién sos.",
                extra={"error": "no_user"},
            )

        uid = int(user.id)
        repo = RecordatoryRepository(self.db)
        text = (user_text or "").strip()

        # Cancelación de un borrado pendiente
        if uid in _pending_delete and (_DENY_RE.search(text) or confirm is False):
            _pending_delete.pop(uid, None)
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=valor,
                spoken_override="Listo, no borro nada.",
                extra={"cancelled": True},
            )

        # Confirmación: flag del LLM o texto afirmativo (sí/dale), no "borra…"
        wants_confirm = bool(confirm) or bool(_AFFIRM_RE.search(text))
        pending_id = _pending_delete.get(uid)

        if wants_confirm and pending_id is not None:
            row = repo.get_active_for_user(uid, pending_id)
            if row is None:
                _pending_delete.pop(uid, None)
                return ActionResult(
                    action=row_clave,
                    metodo="eliminar_recordatorio",
                    valor=valor,
                    spoken_override="Ese recordatorio ya no está. ¿Cuál querés borrar?",
                    extra={"error": "pending_missing"},
                )
            repo.soft_delete(row)
            _pending_delete.pop(uid, None)
            spoken = (
                f"Listo, borré {row.message} del {_format_when(row.scheduled_at)}."
            )
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=row.message,
                spoken_override=spoken,
                extra={
                    "deleted": True,
                    "id": row.id,
                    "message": row.message,
                    "scheduled_at": (
                        row.scheduled_at.isoformat(sep=" ") if row.scheduled_at else None
                    ),
                },
            )

        # Sin valor del LLM no inventar mensaje desde "borra un recordatorio"
        if not (valor or "").strip():
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=valor,
                spoken_override=(
                    "Decime el mensaje del recordatorio, el día y la hora "
                    "para buscarlo."
                ),
                extra={"error": "missing_message"},
            )

        message, scheduled_at = resolve_recordatory_schedule(
            valor=valor,
            fecha=fecha,
            user_text=user_text,
        )
        # Para borrar, la fecha puede ser pasada: matcheamos por mes/día/hora.

        # Si la fecha no parseó, intentar extract del pedido
        if scheduled_at is None:
            scheduled_at, _ = extract_datetime_from_text(
                " ".join(p for p in (user_text, fecha or "") if p)
            )
        if scheduled_at is None:
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=message,
                spoken_override=(
                    f"¿Qué día y hora era el recordatorio de «{message}»?"
                ),
                extra={"error": "missing_fecha", "message": message},
            )

        matches = find_recordatories_for_delete(
            repo,
            user_id=uid,
            message=message,
            scheduled_at=scheduled_at,
        )
        # Si con fecha no hay match, reintentar solo por mensaje
        if not matches and scheduled_at is not None:
            by_msg = find_recordatories_for_delete(
                repo,
                user_id=uid,
                message=message,
                scheduled_at=None,
            )
            if len(by_msg) == 1:
                matches = by_msg

        if not matches:
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=message,
                spoken_override=(
                    f"No encontré un recordatorio de «{message}»"
                    + (
                        f" para el {_format_when(scheduled_at)}."
                        if scheduled_at
                        else "."
                    )
                    + " ¿Me repetís mensaje, día y hora?"
                ),
                extra={"error": "not_found", "message": message},
            )

        if len(matches) > 1:
            parts = [
                f"{i}. {r.message}, {_format_when(r.scheduled_at)}"
                for i, r in enumerate(matches[:5], start=1)
            ]
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=message,
                spoken_override=(
                    "Encontré más de uno. ¿Cuál borro? "
                    + ". ".join(parts)
                    + "."
                ),
                extra={
                    "error": "ambiguous",
                    "items": [
                        {
                            "id": r.id,
                            "message": r.message,
                            "scheduled_at": (
                                r.scheduled_at.isoformat(sep=" ")
                                if r.scheduled_at
                                else None
                            ),
                        }
                        for r in matches[:5]
                    ],
                },
            )

        row = matches[0]

        # Confirmación con los mismos datos
        if wants_confirm or (confirm is True):
            repo.soft_delete(row)
            _pending_delete.pop(uid, None)
            spoken = (
                f"Listo, borré {row.message} del {_format_when(row.scheduled_at)}."
            )
            return ActionResult(
                action=row_clave,
                metodo="eliminar_recordatorio",
                valor=row.message,
                spoken_override=spoken,
                extra={
                    "deleted": True,
                    "id": row.id,
                    "message": row.message,
                    "scheduled_at": (
                        row.scheduled_at.isoformat(sep=" ") if row.scheduled_at else None
                    ),
                },
            )

        _pending_delete[uid] = int(row.id)
        spoken = (
            f"Encontré {row.message} para el {_format_when(row.scheduled_at)}. "
            "¿Confirmás que lo borro?"
        )
        return ActionResult(
            action=row_clave,
            metodo="eliminar_recordatorio",
            valor=row.message,
            spoken_override=spoken,
            extra={
                "needs_confirm": True,
                "id": row.id,
                "message": row.message,
                "scheduled_at": (
                    row.scheduled_at.isoformat(sep=" ") if row.scheduled_at else None
                ),
            },
        )

    async def _listar_recordatorios(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="listar_recordatorios",
                valor=valor,
                spoken_override="No puedo listar recordatorios sin saber quién sos.",
                extra={"error": "no_user", "items": []},
            )

        rows = RecordatoryRepository(self.db).list_for_user(int(user.id))
        items: list[dict] = []
        for row in rows:
            message = (row.message or "").strip()
            if not message:
                continue
            scheduled_at = (
                row.scheduled_at.isoformat(sep=" ") if row.scheduled_at else None
            )
            spec = spec_from_row(
                recurrence=getattr(row, "recurrence", None),
                weekdays=getattr(row, "weekdays", None),
                time_of_day=getattr(row, "time_of_day", None),
            )
            name = _reminder_display_name(row)
            items.append(
                {
                    "id": row.id,
                    # El chat mobile renderiza `item.name` (como tareas/proyectos).
                    "name": name,
                    "message": message,
                    "scheduled_at": scheduled_at,
                    "recurrence": spec.recurrence,
                    "weekdays": spec.weekdays,
                    "time_of_day": spec.time_of_day,
                }
            )

        title = "Tus recordatorios"
        if not items:
            spoken = "No tenés recordatorios pendientes."
            display = spoken
        elif len(items) == 1:
            item = items[0]
            spoken = f"Tenés uno: {item['name']}."
            display = f"{title}:\n• {item['name']}"
        else:
            parts = [f"{i}. {item['name']}" for i, item in enumerate(items, start=1)]
            spoken = f"Tenés {len(items)} recordatorios. " + ". ".join(parts) + "."
            display = title + ":\n" + "\n".join(f"• {item['name']}" for item in items)

        logger.info("Recordatorios user=%s count=%s", user.id, len(items))
        return ActionResult(
            action=row_clave,
            metodo="listar_recordatorios",
            valor=valor,
            spoken_override=spoken,
            extra={
                "count": len(items),
                "items": items,
                "display": display,
                "items_title": title,
            },
        )

    async def _listar_proyectos(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="listar_proyectos",
                valor=valor,
                spoken_override="No puedo listar proyectos sin saber quién sos.",
                extra={"error": "no_user", "items": []},
            )

        rows = ProyectsRepository(self.db).list_for_user(int(user.id))
        items = [{"id": row.id, "name": row.name} for row in rows if (row.name or "").strip()]
        if not items:
            spoken = "No tenés proyectos."
            display = "No tenés proyectos."
        elif len(items) == 1:
            spoken = f"Tenés un proyecto: {items[0]['name']}."
            display = f"Tus proyectos:\n• {items[0]['name']}"
        else:
            spoken = (
                f"Tenés {len(items)} proyectos: "
                + ", ".join(item["name"] for item in items)
                + "."
            )
            display = "Tus proyectos:\n" + "\n".join(
                f"• {item['name']}" for item in items
            )

        logger.info("Proyectos user=%s count=%s", user.id, len(items))
        return ActionResult(
            action=row_clave,
            metodo="listar_proyectos",
            valor=valor,
            spoken_override=spoken,
            extra={
                "count": len(items),
                "items": items,
                "display": display,
                "items_title": "Tus proyectos",
            },
        )

    async def _listar_tareas(
        self,
        *,
        valor: str,
        user: Optional[User],
        user_text: str,
        row_clave: str,
        fecha: str | None = None,
        confirm: bool | None = None,
        valor1: str | None = None,
    ) -> ActionResult:
        if user is None:
            return ActionResult(
                action=row_clave,
                metodo="listar_tareas",
                valor=valor,
                spoken_override="No puedo listar tareas sin saber quién sos.",
                extra={"error": "no_user", "items": []},
            )

        user_id = int(user.id)
        ref = (valor1 or "").strip() or (valor or "").strip()
        projects = ProyectsRepository(self.db).list_for_user(user_id)

        def ask_project(
            choices: list[Proyects],
            *,
            spoken: str,
            display: str,
            title: str,
        ) -> ActionResult:
            _pending_list_tasks[user_id] = True
            items = [
                {"id": row.id, "name": row.name}
                for row in choices
                if (row.name or "").strip()
            ]
            return ActionResult(
                action=row_clave,
                metodo="listar_tareas",
                valor=valor,
                spoken_override=spoken,
                extra={
                    "count": len(items),
                    "items": items,
                    "display": display,
                    "items_title": title,
                    "needs_project": True,
                },
            )

        if not projects:
            _pending_list_tasks.pop(user_id, None)
            spoken = "No tenés proyectos todavía. Primero creá un proyecto para ver sus tareas."
            return ActionResult(
                action=row_clave,
                metodo="listar_tareas",
                valor=valor,
                spoken_override=spoken,
                extra={"count": 0, "items": [], "display": spoken},
            )

        matches = self._resolve_project_ref(user_id, ref) if ref else []
        if not matches and ref and not ref.isdigit():
            matches = _match_projects(projects, ref)

        if not matches:
            if ref.isdigit():
                _pending_list_tasks.pop(user_id, None)
                spoken = "No encontré el proyecto seleccionado para listar las tareas."
                return ActionResult(
                    action=row_clave,
                    metodo="listar_tareas",
                    valor=valor,
                    spoken_override=spoken,
                    extra={"count": 0, "items": [], "display": spoken},
                )
            names = ", ".join(row.name for row in projects if (row.name or "").strip())
            return ask_project(
                projects,
                spoken=f"¿De qué proyecto querés las tareas? Tenés: {names}.",
                display="¿De qué proyecto querés las tareas?",
                title="¿De qué proyecto?",
            )

        if len(matches) > 1:
            names = ", ".join(row.name for row in matches if (row.name or "").strip())
            return ask_project(
                matches,
                spoken=f"Encontré más de un proyecto. ¿Cuál de estos querés? {names}.",
                display="Encontré más de un proyecto. ¿Cuál querés?",
                title="¿Cuál de estos?",
            )

        proyect = matches[0]
        _pending_list_tasks.pop(user_id, None)
        rows = TasksRepository(self.db).list_for_project(user_id, int(proyect.id))
        items = [
            {"id": row.id, "name": row.name}
            for row in rows
            if (row.name or "").strip()
        ]
        title = f"Tareas de {proyect.name}"
        
        if len(items) == 0:
            spoken = f"Vamos a hablar sobre {proyect.name} aún no tiene tareas."
            display = f"aún no tiene tareas."
        elif len(items) == 1:
            spoken = f"Vamos a hablar sobre {proyect.name} tenés una tarea: {items[0]['name']}."
            display = f"{title}:\n• {items[0]['name']}"
        else:
            spoken = (
                f"Vamos a hablar sobre {proyect.name} tenés {len(items)} tareas: "
                + ", ".join(item["name"] for item in items)
                + "."
            )
            display = title + ":\n" + "\n".join(f"• {item['name']}" for item in items)

        logger.info(
            "Tareas user=%s proyect=%s count=%s",
            user.id,
            proyect.id,
            len(items),
        )
        return ActionResult(
            action=row_clave,
            metodo="listar_tareas",
            valor=valor,
            spoken_override=spoken,
            extra={
                "count": len(items),
                "items": items,
                "display": display,
                "items_title": title,
                "proyect_id": proyect.id,
                "proyect_name": proyect.name,
            },
        )


async def run_llm_action(
    db: Session,
    settings: Settings,
    *,
    raw_assistant_text: str,
    user: Optional[User],
    user_text: str = "",
    proyect_id: int | None = None,
) -> tuple[str, ActionResult | None]:
    """Parsea salida LLM, despacha acción si hay, devuelve (texto_tts, result)."""
    parsed = parse_llm_output(raw_assistant_text)
    spoken = parsed.spoken_text
    clave = parsed.clave
    proyect_name = None
    if proyect_id is not None and user is not None:
        try:
            row = ProyectsRepository(db).get_active_for_user(int(user.id), int(proyect_id))
            proyect_name = row.name if row is not None else None
        except (TypeError, ValueError):
            proyect_name = None
    if not clave and wants_list_projects(user_text):
        clave = "listar_proyectos"
    if not clave and wants_list_tasks(user_text, proyect_name):
        clave = "listar_tareas"
    if not clave and wants_add_task(user_text):
        clave = "agregar_tarea"
    if not clave and wants_delete_task(user_text):
        clave = "eliminar_tarea"
    if not clave and wants_select_project(user_text):
        clave = "seleccionar_proyecto"
    if not clave and wants_delete_project(user_text):
        clave = "eliminar_proyecto"
    if not clave and wants_delete_messages(user_text):
        clave = "eliminar_mensajes"
    if not clave:
        return spoken, None

    valor = parsed.valor or ""
    valor1 = parsed.valor1 or ""
    selected = ""
    if proyect_id is not None:
        try:
            selected = str(int(proyect_id))
        except (TypeError, ValueError):
            selected = ""
    if clave == "listar_tareas":
        valor = selected or valor or extract_task_project(user_text)
        if selected:
            valor1 = selected
    if clave == "seleccionar_proyecto" and not valor:
        valor = extract_select_project_name(user_text)
    if clave == "agregar_proyecto" and not valor:
        valor = extract_add_project_name(user_text)
    if clave == "agregar_tarea":
        ext_name, ext_proj = extract_add_task_parts(user_text)
        if not valor:
            valor = ext_name
        valor1 = selected or valor1 or ext_proj
    if clave == "eliminar_tarea":
        if not valor:
            valor = extract_delete_task_name(user_text)
        valor1 = selected or valor1
    if clave == "eliminar_proyecto":
        if not valor:
            valor = extract_delete_project_name(user_text)
        valor1 = ""
    if clave == "eliminar_mensajes":
        valor1 = selected or valor1

    dispatcher = ActionDispatcher(db, settings)
    result = await dispatcher.dispatch(
        clave=clave,
        valor=valor,
        user=user,
        user_text=user_text,
        fecha=parsed.fecha,
        confirm=parsed.confirm,
        valor1=valor1,
        recurrencia=parsed.recurrencia,
        dias=parsed.dias,
    )
    if result and result.spoken_override and not spoken:
        spoken = result.spoken_override
    elif result and result.spoken_override and result.metodo in (
        "listar_recordatorios",
        "listar_proyectos",
        "listar_tareas",
        "agregar_proyecto",
        "seleccionar_proyecto",
        "eliminar_proyecto",
        "agregar_tarea",
        "eliminar_tarea",
        "eliminar_mensajes",
        "agregar_recordatorio",
        "eliminar_recordatorio",
    ):
        # Siempre confirmar con el spoken del sistema (éxito, error, skip/dedupe).
        spoken = result.spoken_override
    return spoken, result
