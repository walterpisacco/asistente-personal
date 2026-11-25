from datetime import datetime
from zoneinfo import ZoneInfo

from app.providers.llm.base import parse_json_object
from app.services.intent_router import IntentRouter, build_router_system, classify_with_regex


def test_parse_json_object_plain_and_fenced():
    assert parse_json_object('{"clave":"none"}')["clave"] == "none"
    assert parse_json_object('```json\n{"clave":"agregar_tarea","valor":"x"}\n```')[
        "valor"
    ] == "x"


def test_from_payload_rejects_unknown_clave():
    decision = IntentRouter._from_payload(
        {"clave": "hack", "valor": "x"},
        {"none", "agregar_tarea"},
        source="router",
    )
    assert decision.clave == "none"
    assert not decision.is_action


def test_from_payload_add_task():
    decision = IntentRouter._from_payload(
        {"clave": "agregar_tarea", "valor": "invitar parientes"},
        {"none", "agregar_tarea", "listar_tareas", "eliminar_tarea"},
        source="router",
    )
    assert decision.is_action
    assert decision.clave == "agregar_tarea"
    assert decision.valor == "invitar parientes"


def test_router_system_tolerates_braces_in_catalog():
    catalog = (
        '[{"clave":"agregar_recordatorio","description":'
        '"JSON {\\"clave\\":\\"agregar_recordatorio\\"}"}]'
    )
    system = build_router_system("none, agregar_recordatorio", catalog)
    assert "agregar_recordatorio" in system
    assert "FECHA_HORA_ACTUAL:" in system


def test_router_system_includes_local_datetime():
    now = datetime(2026, 10, 29, 14, 45, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    system = build_router_system("none, agregar_recordatorio", "[]", now=now)
    assert "FECHA_HORA_ACTUAL:" in system
    assert "datetime: 2026-10-29 14:45" in system
    assert "dia: jueves" in system
    assert "America/Argentina/Buenos_Aires" in system
    assert "lenguaje relativo" not in system


def test_regex_fallback_agregas_and_list():
    add = classify_with_regex("¿Me agregas una tarea Invitar parientes?")
    assert add.clave == "agregar_tarea"
    assert "invitar parientes" in add.valor.lower()

    listing = classify_with_regex(
        "Lista las vacaciones 2027",
        proyect_name="Vacaciones 2027",
    )
    assert listing.clave == "listar_tareas"

    chat = classify_with_regex("hola, cómo estás?")
    assert chat.clave == "none"
    assert not chat.is_action


def test_from_payload_delete_task():
    decision = IntentRouter._from_payload(
        {"clave": "eliminar_tarea", "valor": "comprar pan"},
        {"none", "agregar_tarea", "listar_tareas", "eliminar_tarea"},
        source="router",
    )
    assert decision.is_action
    assert decision.clave == "eliminar_tarea"
    assert decision.valor == "comprar pan"


def test_from_payload_delete_messages():
    decision = IntentRouter._from_payload(
        {"clave": "eliminar_mensajes", "valor": ""},
        {"none", "eliminar_mensajes", "eliminar_proyecto"},
        source="router",
    )
    assert decision.is_action
    assert decision.clave == "eliminar_mensajes"


def test_regex_fallback_delete_messages():
    decision = classify_with_regex("Borrá los mensajes de este proyecto")
    assert decision.clave == "eliminar_mensajes"
    decision = classify_with_regex("Limpiá el chat")
    assert decision.clave == "eliminar_mensajes"
    decision = classify_with_regex("Eliminá la conversación")
    assert decision.clave == "eliminar_mensajes"


def test_from_payload_delete_project():
    decision = IntentRouter._from_payload(
        {"clave": "eliminar_proyecto", "valor": "mi casa"},
        {"none", "agregar_proyecto", "listar_proyectos", "eliminar_proyecto"},
        source="router",
    )
    assert decision.is_action
    assert decision.clave == "eliminar_proyecto"
    assert decision.valor == "mi casa"


def test_regex_fallback_delete_project():
    decision = classify_with_regex("Borrá el proyecto mi casa")
    assert decision.clave == "eliminar_proyecto"
    assert "mi casa" in decision.valor.lower()


def test_regex_fallback_delete_task():
    decision = classify_with_regex(
        "Borrá la tarea comprar pan",
        proyect_name="Compras de fin de año",
    )
    assert decision.clave == "eliminar_tarea"
    assert "comprar pan" in decision.valor.lower()


def test_from_payload_normalizes_reminder_fecha():
    decision = IntentRouter._from_payload(
        {
            "clave": "agregar_recordatorio",
            "valor": "invitar parientes",
            "fecha": "2026-12-24T18:30:00",
        },
        {"none", "agregar_recordatorio"},
        source="router",
    )
    assert decision.clave == "agregar_recordatorio"
    assert decision.valor == "invitar parientes"
    assert decision.fecha == "2026-12-24 18:30"
    assert decision.valor1 == ""


def test_timed_phrase_is_reminder():
    decision = classify_with_regex(
        "comprar vino para la cena mañana a las 7 de la tarde"
    )
    assert decision.clave == "agregar_recordatorio"


def test_regex_fallback_reminder_not_tied_to_project():
    decision = classify_with_regex(
        "Recordame mañana a las 10 llamar al dentista",
        proyect_name="Compras de fin de año",
    )
    assert decision.clave == "agregar_recordatorio"
