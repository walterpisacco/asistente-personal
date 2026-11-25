from app.services.actions import (
    _project_match_score,
    extract_add_task_parts,
    extract_delete_project_name,
    extract_delete_task_name,
    wants_add_task,
    wants_delete_messages,
    wants_delete_project,
    wants_delete_task,
    wants_list_projects,
    wants_list_tasks,
)


SHOULD_LIST = [
    "Lista las tareas",
    "Listá las tareas",
    "Listar las tareas",
    "Listame las tareas",
    "Lista mis tareas",
    "Mostrar las tareas",
    "Mostrame las tareas",
    "Mostrá las tareas",
    "Cuáles son las tareas",
    "Qué tareas hay",
    "Qué tarea tengo",
    "Las tareas",
    "Mis tareas",
    "Tareas",
    "Las tareas de mi casa",
    "Tareas de mi casa",
    "Tareas del proyecto Elian",
    "Otra vez las tareas",
    "De nuevo las tareas",
    "Listá las tareas otra vez",
    "Dame las tareas",
    "Decime las tareas",
    "Decí las tareas",
    "Ver las tareas",
    "las tareas.",
]

SHOULD_NOT_LIST = [
    "Agregá una tarea salud",
    "Agregar tarea al proyecto Elian",
    "Nueva tarea comprar pan",
    "Completé la tarea de regar",
    "La tarea de regar las plantas",
    "Hola cómo estás",
    "Lista mis proyectos",
    "Borrá la tarea comprar pan",
    "Eliminá la tarea invitar parientes",
    "Borrá el proyecto mi casa",
]


def test_list_tasks_phrases_match():
    missed = [text for text in SHOULD_LIST if not wants_list_tasks(text)]
    assert missed == []


def test_list_tasks_does_not_steal_other_intents():
    stolen = [text for text in SHOULD_NOT_LIST if wants_list_tasks(text)]
    assert stolen == []


def test_add_task_still_wins_over_list():
    text = "Agregá una tarea de salud al proyecto Elian"
    assert wants_add_task(text)
    assert not wants_list_projects(text)


def test_repeated_list_request_still_matches():
    first = "Lista las tareas"
    second = "Lista las tareas"
    third = "Mostrar las tareas"
    assert wants_list_tasks(first)
    assert wants_list_tasks(second)
    assert wants_list_tasks(third)


VACACIONES = "Vacaciones 2027"


def test_selected_project_name_without_word_tareas():
    phrases = [
        "Lista las vacaciones 2027",
        "Listá vacaciones 2027",
        "Mostrar vacaciones",
        "Mostrame vacaciones 2027",
        "Lista las de vacaciones",
        "Dame vacaciones 2027",
        "Vacaciones 2027 las tareas",
        "listá las vacaciones dos mil veintisiete",
    ]
    missed = [text for text in phrases if not wants_list_tasks(text, VACACIONES)]
    assert missed == []


def test_bare_list_uses_selected_project():
    assert wants_list_tasks("listá", VACACIONES)
    assert wants_list_tasks("mostrame", "Mi Casa")
    assert wants_list_tasks("lista las", VACACIONES)


def test_talking_about_vacaciones_without_list_hint_goes_to_llm():
    assert not wants_list_tasks("qué hago en vacaciones", VACACIONES)
    assert not wants_list_tasks("vamos a hablar de vacaciones 2027", VACACIONES)


def test_add_task_agregas_conjugation():
    text = "¿Me agregas una tarea Invitar parientes?"
    assert wants_add_task(text)
    assert not wants_list_tasks(text, "Compras de fin de año")
    name, project = extract_add_task_parts(text)
    assert name.lower() == "invitar parientes"
    assert project == ""


def test_delete_task_phrases_match():
    phrases = [
        "Borrá la tarea comprar pan",
        "Eliminá la tarea invitar parientes",
        "Eliminar tarea salud",
        "Sacá la tarea regar las plantas",
        "Quitá la tarea comprar pan",
        "Borrame la tarea invitar parientes",
        "¿Me borrás una tarea comprar pan?",
    ]
    missed = [text for text in phrases if not wants_delete_task(text)]
    assert missed == []
    stolen = [text for text in phrases if wants_list_tasks(text) or wants_add_task(text)]
    assert stolen == []


def test_delete_project_phrases_match():
    phrases = [
        "Borrá el proyecto mi casa",
        "Eliminá el proyecto vacaciones",
        "Eliminar proyecto salud",
        "Sacá el proyecto compras",
        "Borrame el proyecto invitar parientes",
        "¿Me borrás un proyecto mi casa?",
        "Borrá el proyecto",
    ]
    missed = [text for text in phrases if not wants_delete_project(text)]
    assert missed == []
    stolen = [
        text
        for text in phrases
        if wants_list_projects(text) or wants_delete_task(text) or wants_add_task(text)
    ]
    assert stolen == []


def test_delete_messages_phrases_match():
    phrases = [
        "Borrá los mensajes",
        "Eliminá los mensajes",
        "Borrá todos los mensajes",
        "Limpiá el chat",
        "Vaciar el chat",
        "Borrá la conversación",
        "Eliminá el historial",
        "Borrá los mensajes de este proyecto",
        "Limpiá los mensajes",
    ]
    missed = [text for text in phrases if not wants_delete_messages(text)]
    assert missed == []
    stolen = [
        text
        for text in phrases
        if wants_delete_project(text) or wants_delete_task(text)
    ]
    assert stolen == []


def test_delete_messages_does_not_steal_other_deletes():
    assert not wants_delete_messages("Borrá la tarea comprar pan")
    assert not wants_delete_messages("Borrá el proyecto mi casa")
    assert not wants_delete_messages("Borrá el recordatorio del dentista")
    assert not wants_delete_project("Borrá los mensajes del proyecto")
    assert wants_delete_messages("Borrá los mensajes del proyecto")


def test_delete_project_does_not_steal_task_or_list():
    assert not wants_delete_project("Borrá la tarea comprar pan")
    assert not wants_delete_project("Lista mis proyectos")
    assert not wants_delete_project("Agregá un proyecto mi casa")
    assert not wants_delete_project("Borrá el recordatorio del dentista")


def test_default_project_is_protected():
    from app.repositories.proyects_repository import (
        DEFAULT_PROJECT_NAME,
        is_default_project_name,
    )

    assert is_default_project_name(DEFAULT_PROJECT_NAME)
    assert is_default_project_name("temas en general")
    assert not is_default_project_name("Vacaciones")


def test_delete_project_extracts_name():
    assert extract_delete_project_name("Borrá el proyecto mi casa").lower() == "mi casa"
    assert extract_delete_project_name("Eliminá el proyecto").lower() == ""
    assert (
        extract_delete_project_name("Borrá el proyecto Vacaciones 2027").lower()
        == "vacaciones 2027"
    )
    assert (
        extract_delete_project_name(
            "Borrá el proyecto vacaciones dos mil veintisiete"
        ).lower()
        == "vacaciones dos mil veintisiete"
    )


def test_vacaciones_2027_matches_spoken_year():
    name = "Vacaciones 2027"
    assert _project_match_score("Vacaciones 2027", name) >= 0.85
    assert _project_match_score("vacaciones dos mil veintisiete", name) >= 0.85
    assert _project_match_score("Borrá el proyecto Vacaciones 2027", name) >= 0.8
    assert _project_match_score("vacaciones", name) >= 0.8


def test_delete_task_does_not_steal_reminder_or_list():
    assert not wants_delete_task("Borrá el recordatorio del dentista")
    assert not wants_delete_task("Lista las tareas")
    assert not wants_delete_task("Agregá una tarea comprar pan")


def test_delete_task_extracts_name_and_ignores_project_clause():
    assert extract_delete_task_name("Borrá la tarea comprar pan").lower() == "comprar pan"
    name = extract_delete_task_name(
        "Eliminá la tarea invitar parientes del proyecto vacaciones"
    )
    assert name.lower() == "invitar parientes"


def test_add_task_other_conjugations():
    phrases = [
        "Agregás una tarea comprar pan",
        "Me agregás una tarea invitar parientes",
        "Añadí una tarea invitar parientes",
        "Añadir una tarea invitar parientes",
        "Creas una tarea invitar parientes",
        "Anotas una tarea invitar parientes",
    ]
    missed = [text for text in phrases if not wants_add_task(text)]
    assert missed == []
