from datetime import datetime, timedelta, timezone
from typing import Any

PLAN_FREE = "free"
PLAN_BASIC = "basic"
PLAN_PREMIUM = "premium"
DEFAULT_PLAN = PLAN_FREE

PLAN_CLAVES = (PLAN_FREE, PLAN_BASIC, PLAN_PREMIUM)

PLAN_MEMORY_DAYS = {
    PLAN_FREE: 1,
    PLAN_BASIC: 30,
    PLAN_PREMIUM: 365,
}

PLAN_CHARACTER_FLAG = {
    PLAN_FREE: "free_plan",
    PLAN_BASIC: "basic_plan",
    PLAN_PREMIUM: "premium_plan",
}

LLM_HISTORY_MAX_MESSAGES = 80
CHAT_HISTORY_MAX_MESSAGES = 200


def normalize_plan_clave(value: Any) -> str:
    clave = str(value or "").strip().lower()
    if clave in PLAN_MEMORY_DAYS:
        return clave
    return DEFAULT_PLAN


def memory_days_for(user: Any | None) -> int:
    clave = DEFAULT_PLAN
    if user is not None:
        clave = normalize_plan_clave(getattr(user, "plan_clave", None))
    return PLAN_MEMORY_DAYS[clave]


def character_plan_flag(plan_clave: Any) -> str:
    return PLAN_CHARACTER_FLAG[normalize_plan_clave(plan_clave)]


def character_available_for_plan(character: Any, plan_clave: Any) -> bool:
    flag = character_plan_flag(plan_clave)
    return bool(getattr(character, flag, False))


def memory_cutoff(user: Any | None, *, now: datetime | None = None) -> datetime:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt - timedelta(days=memory_days_for(user))
