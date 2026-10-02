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
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

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
    r"record[aá]me|acordate"
    r"|anot[aá](me|lo|la)?"
    r"|agreg[aá](me|lo|la)?"
    r"|pon[eé](me)?"
    r"|apunt[aá](me|lo|la)?"
    r"|quiero\s+(un\s+)?recordatorio"
    r"|nuevo\s+recordatorio"
    r"|cre[aá]\s+(un\s+)?recordatorio"
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


def ensure_future_datetime(
    dt: datetime | None,
    *,
    user_text: str = "",
    fecha: str = "",
) -> datetime | None:
    """Si la fecha quedó en el pasado y el usuario no dijo ese año, avanza años.

    Cubre el caso típico: el LLM inventa 2023-11-12 cuando el usuario dijo
    «el 12 de noviembre» (sin año) y hoy es 2026-10-02 → 2026-11-12.
    """
    if dt is None:
        return None
    now = _now_local()
    if dt >= now - timedelta(minutes=1):
        if dt.year <= now.year + 2 or _EXPLICIT_YEAR_RE.search(user_text or ""):
            return dt
        for year in range(now.year, now.year + 3):
            try:
                candidate = dt.replace(year=year)
            except ValueError:
                continue
            if candidate >= now - timedelta(minutes=1):
                logger.info("Ajustando año absurdo %s → %s", dt, candidate)
                return candidate
        return dt

    if _EXPLICIT_YEAR_RE.search(user_text or ""):
        return dt

    candidate = dt
    # Hora de hoy/ayer ya pasada → sumar días (no años). Evita 15:00 → 2027.
    if (now - candidate) <= timedelta(days=2):
        for _ in range(3):
            if candidate >= now - timedelta(minutes=1):
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

    candidate = dt
    for _ in range(6):
        if candidate >= now - timedelta(minutes=1):
            if candidate != dt:
                logger.info(
                    "Ajustando fecha pasada %s → %s (user_text=%r fecha_llm=%r)",
                    dt,
                    candidate,
                    user_text,
                    fecha,
                )
            return candidate
        try:
            candidate = candidate.replace(year=candidate.year + 1)
        except ValueError:
            candidate = candidate.replace(year=candidate.year + 1, day=28)
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
            return clock
    return None


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


def parse_scheduled_at(raw: str | None) -> datetime | None:
    """Parsea fecha/hora (relativa ES, ISO, dd/mm o dateparser)."""
    text = (raw or "").strip()
    if not text:
        return None

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
    """Resuelve mensaje + fecha obligatoria.

    Prioridad: expresiones relativas del usuario (mañana/hoy) sobre el ISO del LLM,
    que a menudo inventa mal la fecha absoluta.
    """
    message, scheduled_from_valor = _parse_recordatory_valor(valor)
    if not message:
        message = (user_text or "").strip()

    combined = " ".join(p for p in (user_text, fecha or "", message) if p)

    # 1) Relativos del pedido del usuario (fuente de verdad frente al ISO del LLM).
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

    # 2) Campo fecha / JSON en valor
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

        message, scheduled_at = resolve_recordatory_schedule(
            valor=valor,
            fecha=fecha,
            user_text=user_text,
        )
        if not message:
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=valor,
                spoken_override="No entendí qué tengo que recordarte.",
                extra={"error": "empty_message"},
            )
        if scheduled_at is None:
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

        repo = RecordatoryRepository(self.db)
        existing = repo.find_active_duplicate(
            user_id=int(user.id),
            message=message,
            scheduled_at=scheduled_at,
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
            return ActionResult(
                action=row_clave,
                metodo="agregar_recordatorio",
                valor=message,
                spoken_override=spoken,
                extra={
                    "id": existing.id,
                    "message": existing.message,
                    "scheduled_at": scheduled_at.isoformat(sep=" "),
                    "deduped": True,
                },
            )

        row = repo.create(
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
        extra = result.extra or {}
        force_override = bool(
            extra.get("skipped")
            or extra.get("deduped")
            or result.metodo == "dame_recordatorios"
        )
        if force_override or len(spoken) < 8:
            spoken = result.spoken_override
    return spoken, result
