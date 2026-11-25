"""Recurrencia de recordatorios: daily / weekly / weekends + próxima ocurrencia."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

RECURRENCE_ONCE = "once"
RECURRENCE_DAILY = "daily"
RECURRENCE_WEEKLY = "weekly"
RECURRENCE_WEEKENDS = "weekends"

# ISO: lunes=1 … domingo=7
_DAY_NAME_TO_ISO = {
    "lunes": 1,
    "martes": 2,
    "miercoles": 3,
    "miércoles": 3,
    "jueves": 4,
    "viernes": 5,
    "sabado": 6,
    "sábado": 6,
    "domingo": 7,
}

_DAY_ISO_TO_NAME = {
    1: "lunes",
    2: "martes",
    3: "miércoles",
    4: "jueves",
    5: "viernes",
    6: "sábado",
    7: "domingo",
}

_WEEKENDS = (6, 7)

_RECURRENCE_DAILY_RE = re.compile(
    r"(?i)\b(?:todos?\s+los\s+d[ií]as|cada\s+d[ií]a|diario|diariamente)\b"
)
_RECURRENCE_WEEKENDS_RE = re.compile(
    r"(?i)\b(?:todos?\s+los\s+fines?\s+de\s+semana|cada\s+fin\s+de\s+semana|"
    r"fines?\s+de\s+semana|s[aá]bados?\s+y\s+domingos?)\b"
)
_RECURRENCE_WEEKDAYS_WORK_RE = re.compile(
    r"(?i)\b(?:de\s+lunes\s+a\s+viernes|lunes\s+a\s+viernes|"
    r"d[ií]as?\s+h[aá]biles?|entre\s+semana)\b"
)
_RECURRENCE_WEEKLY_DAY_RE = re.compile(
    r"(?i)\b(?:todos?\s+los|cada)\s+"
    r"(?P<day>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)\b"
)
_RECURRENCE_TOKEN_RE = re.compile(
    r"(?i)^(once|daily|weekly|weekends|diario|semanal)$"
)


@dataclass(frozen=True)
class RecurrenceSpec:
    recurrence: str = RECURRENCE_ONCE
    weekdays: str | None = None  # "1" / "1,2,3,4,5" / "6,7"
    time_of_day: str | None = None  # "HH:MM"

    @property
    def is_recurring(self) -> bool:
        return (self.recurrence or RECURRENCE_ONCE) != RECURRENCE_ONCE

    def weekday_set(self) -> set[int]:
        raw = (self.weekdays or "").strip()
        if not raw:
            if self.recurrence == RECURRENCE_WEEKENDS:
                return set(_WEEKENDS)
            return set()
        out: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                day = int(part)
            except ValueError:
                continue
            if 1 <= day <= 7:
                out.add(day)
        return out


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def normalize_time_of_day(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def parse_time_of_day(raw: str | None) -> tuple[int, int] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Acepta "9:00", "09:00", "9"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?$", text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def weekdays_csv(days: set[int] | list[int] | tuple[int, ...]) -> str:
    return ",".join(str(d) for d in sorted({int(x) for x in days if 1 <= int(x) <= 7}))


def parse_recurrence_token(raw: str | None) -> RecurrenceSpec | None:
    """Interpreta valor del router: daily / weekly / weekends / once."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in {"once", "puntual", "unico", "único"}:
        return RecurrenceSpec(recurrence=RECURRENCE_ONCE)
    if text in {"daily", "diario", "diaria", "diariamente"}:
        return RecurrenceSpec(recurrence=RECURRENCE_DAILY)
    if text in {"weekends", "weekend", "fines", "finde"}:
        return RecurrenceSpec(
            recurrence=RECURRENCE_WEEKENDS,
            weekdays=weekdays_csv(_WEEKENDS),
        )
    if text in {"weekly", "semanal"}:
        return RecurrenceSpec(recurrence=RECURRENCE_WEEKLY)
    # weekly:lunes / weekly:1,2
    if text.startswith("weekly:"):
        rest = text.split(":", 1)[1].strip()
        days = _days_from_token(rest)
        if days:
            return RecurrenceSpec(
                recurrence=RECURRENCE_WEEKLY,
                weekdays=weekdays_csv(days),
            )
    return None


def _days_from_token(raw: str) -> set[int]:
    text = (raw or "").strip().lower()
    if not text:
        return set()
    if re.fullmatch(r"[\d,]+", text):
        out: set[int] = set()
        for part in text.split(","):
            try:
                day = int(part.strip())
            except ValueError:
                continue
            if 1 <= day <= 7:
                out.add(day)
        return out
    # nombres
    out = set()
    for name, iso in _DAY_NAME_TO_ISO.items():
        if name in text:
            out.add(iso)
    return out


def extract_recurrence_from_text(text: str) -> RecurrenceSpec | None:
    """Detecta recurrencia en español en el pedido del usuario."""
    raw = (text or "").strip()
    if not raw:
        return None
    if _RECURRENCE_DAILY_RE.search(raw):
        return RecurrenceSpec(recurrence=RECURRENCE_DAILY)
    if _RECURRENCE_WEEKENDS_RE.search(raw):
        return RecurrenceSpec(
            recurrence=RECURRENCE_WEEKENDS,
            weekdays=weekdays_csv(_WEEKENDS),
        )
    if _RECURRENCE_WEEKDAYS_WORK_RE.search(raw):
        return RecurrenceSpec(
            recurrence=RECURRENCE_WEEKLY,
            weekdays=weekdays_csv({1, 2, 3, 4, 5}),
        )
    m = _RECURRENCE_WEEKLY_DAY_RE.search(raw)
    if m:
        day_raw = (
            m.group("day")
            .lower()
            .rstrip("s")
            .replace("á", "a")
            .replace("é", "e")
        )
        for name, iso in _DAY_NAME_TO_ISO.items():
            key = name.replace("á", "a").replace("é", "e").rstrip("s")
            if day_raw == key or day_raw.startswith(key) or key.startswith(day_raw):
                return RecurrenceSpec(
                    recurrence=RECURRENCE_WEEKLY,
                    weekdays=weekdays_csv({iso}),
                )
    return None


def strip_recurrence_fragment(message: str) -> str:
    """Saca del mensaje las frases de recurrencia/hora para dejar el texto a recordar."""
    cleaned = message or ""
    for pattern in (
        _RECURRENCE_DAILY_RE,
        _RECURRENCE_WEEKENDS_RE,
        _RECURRENCE_WEEKDAYS_WORK_RE,
        _RECURRENCE_WEEKLY_DAY_RE,
    ):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(
        r"(?i)\b(?:recordame|recordá|recorda|acordate|acordame)\b",
        " ",
        cleaned,
    )
    # Quitar hora típica: "a las 9", "a las 09:00", "9:00 hs"
    cleaned = re.sub(
        r"(?i)\b(?:a\s+)?(?:las?\s+)?\d{1,2}(?::\d{2})?\s*(?:hs|hrs?|horas?)?\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\b(?:de|por)\s+la\s+(?:tarde|noche|mañana)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-")
    return cleaned


def resolve_recurrence(
    *,
    user_text: str = "",
    message: str = "",
    router_recurrence: str | None = None,
    router_days: str | None = None,
) -> RecurrenceSpec:
    """Prioridad: router → texto usuario → once."""
    from_router = parse_recurrence_token(router_recurrence)
    if from_router is not None:
        if from_router.recurrence == RECURRENCE_WEEKLY and not from_router.weekdays:
            days = _days_from_token(router_days or "") or extract_recurrence_from_text(
                " ".join(p for p in (user_text, message) if p)
            )
            if isinstance(days, RecurrenceSpec):
                return RecurrenceSpec(
                    recurrence=RECURRENCE_WEEKLY,
                    weekdays=days.weekdays,
                    time_of_day=from_router.time_of_day,
                )
            if days:
                return RecurrenceSpec(
                    recurrence=RECURRENCE_WEEKLY,
                    weekdays=weekdays_csv(days),
                )
        if from_router.recurrence == RECURRENCE_WEEKLY and router_days:
            days = _days_from_token(router_days)
            if days:
                return RecurrenceSpec(
                    recurrence=RECURRENCE_WEEKLY,
                    weekdays=weekdays_csv(days),
                )
        return from_router

    combined = " ".join(p for p in (user_text, message) if p)
    found = extract_recurrence_from_text(combined)
    if found is not None:
        return found
    return RecurrenceSpec(recurrence=RECURRENCE_ONCE)


def next_occurrence(
    spec: RecurrenceSpec,
    *,
    after: datetime | None = None,
    inclusive: bool = False,
) -> datetime | None:
    """Próximo datetime local según recurrencia + time_of_day.

    inclusive=False: estrictamente después de `after`.
    inclusive=True: permite el mismo instante si cae exacto en after.
    """
    clock = parse_time_of_day(spec.time_of_day)
    if clock is None:
        return None
    hour, minute = clock
    base = after or now_local()
    slack = timedelta(seconds=0 if inclusive else 1)

    if spec.recurrence == RECURRENCE_DAILY:
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < base + slack:
            candidate = candidate + timedelta(days=1)
        return candidate

    days = spec.weekday_set()
    if spec.recurrence in {RECURRENCE_WEEKLY, RECURRENCE_WEEKENDS}:
        if not days:
            if spec.recurrence == RECURRENCE_WEEKENDS:
                days = set(_WEEKENDS)
            else:
                return None
        # Buscar hasta 8 días adelante
        for offset in range(0, 8):
            day = base + timedelta(days=offset)
            if day.isoweekday() not in days:
                continue
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate >= base + slack:
                return candidate
        return None

    # once: no calcula; el caller usa scheduled_at puntual
    return None


def advance_after_fire(spec: RecurrenceSpec, *, from_dt: datetime) -> datetime | None:
    """Siguiente ocurrencia luego de haber disparado `from_dt`."""
    if not spec.is_recurring:
        return None
    return next_occurrence(spec, after=from_dt, inclusive=False)


def describe_recurrence(spec: RecurrenceSpec) -> str:
    """Texto humano corto para TTS / listado."""
    time_label = spec.time_of_day or ""
    if spec.recurrence == RECURRENCE_DAILY:
        return f"todos los días a las {time_label}".strip()
    if spec.recurrence == RECURRENCE_WEEKENDS:
        return f"los fines de semana a las {time_label}".strip()
    if spec.recurrence == RECURRENCE_WEEKLY:
        days = sorted(spec.weekday_set())
        if days == [1, 2, 3, 4, 5]:
            return f"de lunes a viernes a las {time_label}".strip()
        if len(days) == 1:
            name = _DAY_ISO_TO_NAME.get(days[0], str(days[0]))
            return f"todos los {name} a las {time_label}".strip()
        names = [_DAY_ISO_TO_NAME.get(d, str(d)) for d in days]
        return f"los {', '.join(names)} a las {time_label}".strip()
    return time_label


def spec_from_row(
    *,
    recurrence: str | None,
    weekdays: str | None,
    time_of_day: str | None,
) -> RecurrenceSpec:
    rec = (recurrence or RECURRENCE_ONCE).strip().lower() or RECURRENCE_ONCE
    if rec == RECURRENCE_WEEKENDS and not weekdays:
        weekdays = weekdays_csv(_WEEKENDS)
    return RecurrenceSpec(
        recurrence=rec,
        weekdays=(weekdays or None),
        time_of_day=(time_of_day or None),
    )
