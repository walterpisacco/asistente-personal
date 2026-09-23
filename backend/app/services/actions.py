from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import Settings
from app.models.user import User
from app.services.youtube_service import YouTubeService


@dataclass
class ActionResult:
    action: str
    target: str | None = None
    spoken_override: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "spoken_override": self.spoken_override,
            "extra": self.extra or {},
        }


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


async def match_and_run_actions(
    *,
    assistant_text: str,
    user_text: str,
    user: Optional[User],
    settings: Settings,
) -> ActionResult | None:
    triggers = settings.resolved_action_triggers
    haystack = f"{_normalize(assistant_text)} {_normalize(user_text)}"
    for trigger in triggers:
        phrase = _normalize(str(trigger.get("phrase") or ""))
        action = str(trigger.get("action") or "").strip()
        if not phrase or not action:
            continue
        if phrase not in haystack and phrase not in _normalize(user_text):
            # También detectar pedidos de música en el texto del usuario.
            if action != "youtube_play":
                continue
            music_hints = (
                "poné música",
                "pone musica",
                "poneme una canción",
                "reproducí",
                "reproduce",
                "quiero música",
                "quiero musica",
                "poné una canción",
                "pone una cancion",
            )
            if not any(h in _normalize(user_text) for h in music_hints):
                continue
        target = trigger.get("target")
        if action == "youtube_play":
            yt = YouTubeService(settings)
            result = await yt.play_from_profile(
                user=user,
                query_hint=user_text,
                target=str(target or "random"),
            )
            return ActionResult(
                action=action,
                target=str(target) if target else None,
                spoken_override=result.get("spoken"),
                extra=result,
            )
        return ActionResult(action=action, target=str(target) if target else None)
    return None
