"""Tests unitarios de recurrencia de recordatorios."""

from datetime import datetime

from app.services.recordatory_recurrence import (
    RECURRENCE_DAILY,
    RECURRENCE_WEEKENDS,
    RECURRENCE_WEEKLY,
    RecurrenceSpec,
    describe_recurrence,
    extract_recurrence_from_text,
    next_occurrence,
    resolve_recurrence,
    strip_recurrence_fragment,
)


def test_extract_daily():
    spec = extract_recurrence_from_text("recordame todos los dias a las 9 la pastilla")
    assert spec is not None
    assert spec.recurrence == RECURRENCE_DAILY


def test_extract_weekly_monday():
    spec = extract_recurrence_from_text("recordame todos los lunes a las 18 el standup")
    assert spec is not None
    assert spec.recurrence == RECURRENCE_WEEKLY
    assert spec.weekdays == "1"


def test_extract_weekends():
    spec = extract_recurrence_from_text("recordame los fines de semana a las 10 pasear")
    assert spec is not None
    assert spec.recurrence == RECURRENCE_WEEKENDS
    assert spec.weekdays == "6,7"


def test_strip_recurrence_keeps_message():
    cleaned = strip_recurrence_fragment(
        "recordame todos los dias a las 9 tomar la pastilla"
    )
    assert "pastilla" in cleaned.lower()
    assert "todos" not in cleaned.lower()


def test_next_daily_after_hour():
    spec = RecurrenceSpec(recurrence=RECURRENCE_DAILY, time_of_day="09:00")
    after = datetime(2026, 11, 11, 10, 0, 0)
    nxt = next_occurrence(spec, after=after, inclusive=False)
    assert nxt == datetime(2026, 11, 12, 9, 0, 0)


def test_next_weekly_monday():
    spec = RecurrenceSpec(
        recurrence=RECURRENCE_WEEKLY,
        weekdays="1",
        time_of_day="18:00",
    )
    # 2026-11-11 es miércoles
    after = datetime(2026, 11, 11, 12, 0, 0)
    nxt = next_occurrence(spec, after=after, inclusive=False)
    assert nxt == datetime(2026, 11, 16, 18, 0, 0)
    assert nxt.isoweekday() == 1


def test_resolve_router_weekly():
    spec = resolve_recurrence(
        user_text="recordame todos los lunes a las 18",
        router_recurrence="weekly",
        router_days="lunes",
    )
    assert spec.recurrence == RECURRENCE_WEEKLY
    assert spec.weekdays == "1"


def test_describe_daily():
    spec = RecurrenceSpec(recurrence=RECURRENCE_DAILY, time_of_day="09:00")
    assert "todos los días" in describe_recurrence(spec)
    assert "09:00" in describe_recurrence(spec)


if __name__ == "__main__":
    test_extract_daily()
    test_extract_weekly_monday()
    test_extract_weekends()
    test_strip_recurrence_keeps_message()
    test_next_daily_after_hour()
    test_next_weekly_monday()
    test_resolve_router_weekly()
    test_describe_daily()
    print("ok")
