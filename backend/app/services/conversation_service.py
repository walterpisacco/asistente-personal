import json
import logging
import time
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.actions import Action
from app.models.character import Character
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.user import User
from app.providers.llm.factory import create_llm_provider
from app.providers.stt.factory import create_stt_provider
from app.providers.tts.factory import create_tts_provider
from app.repositories.action_repository import ActionRepository
from app.repositories.character_repository import CharacterRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.user_repository import UserRepository
from app.services.actions import run_llm_action
from app.services.audio_service import AudioService

logger = logging.getLogger(__name__)

_USER_CONTEXT_INSTRUCTIONS = """
REGLAS_USUARIO (prioridad sobre otras instrucciones):
Usá el siguiente JSON CRM como fuente de verdad del interlocutor.
- Saludá y dirigite por full_name cuando exista.
- No inventes datos que no estén en el JSON.
- Nunca menciones IDs técnicos ni password.
""".strip()


def build_user_crm(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "gender": user.gender,
        "age": user.age,
        "youtube_profile": user.youtube_profile,
    }


def build_actions_catalog(actions: list[Action]) -> str:
    """JSON compacto de la tabla actions para que el LLM elija clave y valor."""
    payload = [
        {
            "clave": row.clave,
            "description": row.description,
            "valor": row.valor,
        }
        for row in actions
    ]
    return (
        "ACCIONES_DISPONIBLES:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def build_llm_system_prompt(
    character: Character,
    user: Optional[User],
    file_prompt: str = "",
    actions: list[Action] | None = None,
) -> str:
    parts: list[str] = []
    base = (file_prompt or "").strip() or (character.system_prompt or "").strip()
    if base:
        parts.append(base)
    if actions is not None:
        parts.append(build_actions_catalog(actions))
    if user:
        parts.append(_USER_CONTEXT_INSTRUCTIONS)
        parts.append(
            "USER_CONTEXT:\n"
            + json.dumps(build_user_crm(user), ensure_ascii=False, separators=(",", ":"))
        )
    return "\n\n".join(p for p in parts if p)


class ConversationService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.conversations = ConversationRepository(db)
        self.characters = CharacterRepository(db)
        self.users = UserRepository(db)
        self.audio = AudioService(self.settings)
        self.llm = create_llm_provider(self.settings)
        self.stt = create_stt_provider(self.settings)
        self.tts = create_tts_provider(self.settings)

    def _actions(self) -> list[Action]:
        return ActionRepository(self.db).list_all()

    def _tts_extension(self) -> str:
        if self.settings.tts_provider == "mock":
            return "wav"
        return "mp3"

    async def start_conversation(
        self,
        user: User,
        character_id: str | None = None,
        device_id: str | None = None,
    ) -> tuple[Conversation, bytes, str]:
        if character_id:
            character = self.characters.get_by_id(str(character_id))
        else:
            character = self.characters.first()
        if not character:
            raise RuntimeError("No hay un personaje en la base para iniciar la conversación")

        conversation = Conversation(
            id=f"conv_{uuid.uuid4().hex[:10]}",
            user_id=user.id,
            character_id=character.id,
            status="active",
            device_id=device_id,
        )
        saved = self.conversations.create(conversation)

        if self.settings.bot_opening_mode == "llm":
            system_prompt = build_llm_system_prompt(
                character,
                user,
                self.settings.resolved_bot_system_prompt,
                self._actions(),
            )
            intro_text = await self.llm.generate(
                [{"role": "user", "content": "Saludame brevemente para empezar."}],
                system_prompt,
            )
        else:
            name = (user.full_name or "").strip()
            greeting = self.settings.bot_greeting.strip() or "Hola"
            intro_text = f"{greeting}{', ' + name if name else ''}. ¿En qué te ayudo?"

        audio_content = await self.tts.synthesize(intro_text, character.voice_id)
        audio_path = await self.audio.save_audio(
            audio_content, extension=self._tts_extension()
        )
        self.conversations.add_message(
            ConversationMessage(
                conversation_id=saved.id,
                role="assistant",
                text=intro_text,
                audio_url=audio_path,
            )
        )
        return saved, audio_content, intro_text

    async def process_audio(
        self,
        conversation_id: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        conversation = self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise RuntimeError(f"Conversation not found: {conversation_id}")

        character = self.characters.get_by_id(conversation.character_id)
        if not character:
            raise RuntimeError(f"Character not found: {conversation.character_id}")

        user = self.users.get_by_id(conversation.user_id)
        latency: dict[str, Any] = {}

        t0 = time.perf_counter()
        user_text = await self.stt.transcribe(
            audio_bytes, language=self.settings.stt_language
        )
        latency["stt_ms"] = int((time.perf_counter() - t0) * 1000)
        if not user_text:
            return {
                "conversation_id": conversation.id,
                "user_text": "",
                "assistant_text": "",
                "audio_bytes": b"",
                "audio_path": None,
                "latency": latency,
                "action": None,
            }

        self.conversations.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                role="user",
                text=user_text,
            )
        )

        history = [
            {"role": m.role, "content": m.text}
            for m in self.conversations.get_by_id(conversation.id).messages  # type: ignore[union-attr]
        ]
        system_prompt = build_llm_system_prompt(
            character,
            user,
            self.settings.resolved_bot_system_prompt,
            self._actions(),
        )

        t1 = time.perf_counter()
        raw_assistant = await self.llm.generate(history, system_prompt)
        latency["llm_ms"] = int((time.perf_counter() - t1) * 1000)

        assistant_text, action_result = await run_llm_action(
            self.db,
            self.settings,
            raw_assistant_text=raw_assistant,
            user=user,
            user_text=user_text,
        )
        if not assistant_text.strip():
            assistant_text = "Dale."

        t2 = time.perf_counter()
        audio_content = await self.tts.synthesize(assistant_text, character.voice_id)
        latency["tts_ms"] = int((time.perf_counter() - t2) * 1000)
        latency["total_ms"] = sum(
            v for k, v in latency.items() if k.endswith("_ms") and isinstance(v, int)
        )

        audio_path = await self.audio.save_audio(
            audio_content, extension=self._tts_extension()
        )
        self.conversations.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                text=assistant_text,
                audio_url=audio_path,
                latency_ms=latency,
            )
        )

        logger.info(
            "conversation=%s stt=%sms llm=%sms tts=%sms",
            conversation.id,
            latency.get("stt_ms"),
            latency.get("llm_ms"),
            latency.get("tts_ms"),
        )

        return {
            "conversation_id": conversation.id,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "audio_bytes": audio_content,
            "audio_path": audio_path,
            "latency": latency,
            "action": action_result.to_dict() if action_result else None,
        }

    async def transcribe_only(self, audio_bytes: bytes) -> str:
        return await self.stt.transcribe(audio_bytes, language=self.settings.stt_language)

    def end_conversation(self, conversation_id: str) -> None:
        self.conversations.update_status(conversation_id, "ended")
