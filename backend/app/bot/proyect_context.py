"""Proyecto seleccionado en la UI del bot (equivalente al focus de mobile)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.bot.session import load_session
from app.core.database import SessionLocal
from app.models.user import User
from app.repositories.proyects_repository import ProyectsRepository

logger = logging.getLogger("tori.proyect")

_BACKEND = Path(__file__).resolve().parents[2]
SELECTED_PATH = _BACKEND / "data" / "bot_selected_proyect.json"


def get_selected_proyect_id() -> Optional[int]:
    if not SELECTED_PATH.is_file():
        return None
    try:
        data = json.loads(SELECTED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        value = int(data.get("proyect_id"))
    except (TypeError, ValueError, AttributeError):
        return None
    return value if value > 0 else None


def set_selected_proyect_id(proyect_id: int) -> None:
    SELECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTED_PATH.write_text(
        json.dumps({"proyect_id": int(proyect_id)}, ensure_ascii=False),
        encoding="utf-8",
    )


def list_proyects_for_session_user() -> list[dict[str, Any]]:
    """Lista proyectos del usuario logueado (para la UI idle)."""
    session = load_session()
    if session is None or session.user_id <= 0:
        return []
    db = SessionLocal()
    try:
        repo = ProyectsRepository(db)
        repo.ensure_default_for_user(int(session.user_id))
        rows = repo.list_for_user(int(session.user_id))
        out = [
            {"id": int(row.id), "name": (row.name or "").strip() or f"Proyecto {row.id}"}
            for row in rows
        ]
        selected = get_selected_proyect_id()
        ids = {p["id"] for p in out}
        if selected is None or selected not in ids:
            if out:
                set_selected_proyect_id(int(out[0]["id"]))
                selected = int(out[0]["id"])
        return [{"id": p["id"], "name": p["name"], "selected": p["id"] == selected} for p in out]
    except Exception:
        logger.exception("No se pudieron listar proyectos para la UI")
        return []
    finally:
        db.close()


def resolve_proyect_id_for_user(db: Session, user: User | None) -> Optional[int]:
    """Devuelve el proyect_id seleccionado si es del usuario; si no, el default."""
    if user is None:
        return None
    repo = ProyectsRepository(db)
    selected = get_selected_proyect_id()
    if selected is not None:
        row = repo.get_active_for_user(int(user.id), int(selected))
        if row is not None:
            return int(row.id)
    try:
        return int(repo.ensure_default_for_user(int(user.id)).id)
    except Exception:
        logger.debug("Sin proyecto default", exc_info=True)
        return None
