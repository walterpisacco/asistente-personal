"""Sesión local del bot (mismo criterio que mobile: token + usuario)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("tori.session")

_BACKEND = Path(__file__).resolve().parents[2]
SESSION_PATH = _BACKEND / "data" / "bot_session.json"


@dataclass
class BotSession:
    access_token: str
    user_id: int
    username: str
    alias: str = ""
    full_name: str = ""
    remembered_username: str = ""
    remembered_password: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "user_id": self.user_id,
            "username": self.username,
            "alias": self.alias,
            "full_name": self.full_name,
            "remembered_username": self.remembered_username,
            "remembered_password": self.remembered_password,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["BotSession"]:
        try:
            user_id = int(data.get("user_id"))
        except (TypeError, ValueError):
            return None
        token = (data.get("access_token") or "").strip()
        username = (data.get("username") or "").strip()
        if not token or not username or user_id <= 0:
            return None
        return cls(
            access_token=token,
            user_id=user_id,
            username=username,
            alias=(data.get("alias") or "").strip(),
            full_name=(data.get("full_name") or "").strip(),
            remembered_username=(data.get("remembered_username") or "").strip(),
            remembered_password=data.get("remembered_password") or "",
        )


def load_session() -> Optional[BotSession]:
    if not SESSION_PATH.is_file():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("No pude leer sesión: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    return BotSession.from_dict(data)


def save_session(session: BotSession) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(
        json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_session(*, keep_credentials: bool = False) -> None:
    previous = load_session() if keep_credentials else None
    if SESSION_PATH.is_file():
        try:
            SESSION_PATH.unlink()
        except OSError as exc:
            logger.warning("No pude borrar sesión: %s", exc)
    if keep_credentials and previous and previous.remembered_username:
        save_session(
            BotSession(
                access_token="",
                user_id=0,
                username="",
                remembered_username=previous.remembered_username,
                remembered_password=previous.remembered_password,
            )
        )


def cached_credentials() -> tuple[str, str]:
    session = load_session()
    if session is None:
        return "", ""
    return session.remembered_username, session.remembered_password
