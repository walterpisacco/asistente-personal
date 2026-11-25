import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.plans import LLM_HISTORY_MAX_MESSAGES, memory_cutoff
from app.core.database import SessionLocal
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
from app.repositories.proyects_repository import ProyectsRepository
from app.repositories.user_repository import UserRepository
from app.schemas.conversation import (
    AudioConversationResponse,
    ConversationCreate,
    ConversationOut,
    MessageOut,
)
from app.schemas.metrics import MetricEvent
from app.services.actions import (
    ActionDispatcher,
    extract_add_project_name,
    extract_add_task_parts,
    extract_delete_project_name,
    extract_delete_task_name,
    extract_select_project_name,
    pending_intent_context,
    run_llm_action,
)
from app.services.intent_router import IntentDecision, IntentRouter
from app.services.audio_service import AudioService
from app.services.metrics_service import MetricsService
from app.services.tts_stream import TtsByteStream, open_tts_stream

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()

_TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
_DIAS_ES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)


def build_current_datetime_context(
    now: datetime | None = None,
    *,
    for_router: bool = False,
) -> str:
    """Bloque de fecha/hora local para el system prompt del LLM."""
    dt = now or datetime.now(_TZ_AR)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_AR)
    else:
        dt = dt.astimezone(_TZ_AR)
    lines = [
        "FECHA_HORA_ACTUAL:",
        f"- datetime: {dt.strftime('%Y-%m-%d %H:%M')}",
        f"- dia: {_DIAS_ES[dt.weekday()]}",
        "- timezone: America/Argentina/Buenos_Aires",
        "- Usá esta referencia para interpretar hoy, mañana, días de la semana y fechas sin año.",
    ]
    if not for_router:
        lines.append(
            "- Para recordatorios, preferí el lenguaje relativo del usuario (ej. mañana 16:30); "
            "el backend valida y corrige el calendario."
        )
    return "\n".join(lines)


_MEMORY_INSTRUCTIONS = """
MEMORIA_CONVERSACIONAL (obligatorio):
- Recibís el historial completo de esta conversación (mensajes user/assistant previos).
- Usalo para mantener el hilo: recordá nombres, preferencias y lo ya hablado.
- No digas que no tenés memoria o que no recordás lo anterior de esta misma charla.
- Respondé en continuidad con lo previo; no reinicies la conversación como si fuera el primer mensaje.
""".strip()


def build_llm_system_prompt(
    character: Character,
    user_profile: str | None = None,
) -> str:
    parts: list[str] = []
    base = (character.system_prompt or "").strip()
    if base:
        parts.append(base)
    parts.append(build_current_datetime_context())
    profile = (user_profile or "").strip()
    if profile:
        parts.append("PERFIL_DEL_USUARIO:\n" + profile)
    parts.append(_MEMORY_INSTRUCTIONS)
    return "\n\n".join(p for p in parts if p)


_USER_CONTEXT_INSTRUCTIONS = """
CONTEXTO_DE_USUARIO:
- USER_CONTEXT trae datos del interlocutor (CRM). Usalos con naturalidad.
- No inventes datos que no estén ahí.
""".strip()


def build_user_crm(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "gender": user.gender,
        "age": getattr(user, "age", None),
    }


def build_actions_catalog(actions: list[Action]) -> str:
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


def build_bot_llm_system_prompt(
    character: Character,
    user: Optional[User],
    file_prompt: str = "",
    actions: list[Action] | None = None,
) -> str:
    parts: list[str] = []
    base = (file_prompt or "").strip() or (character.system_prompt or "").strip()
    if base:
        parts.append(base)
    parts.append(build_current_datetime_context())
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
        self.metrics = MetricsService(db)
        self.audio = AudioService(self.settings)
        self.llm = create_llm_provider(self.settings)
        self.intent = IntentRouter(self.llm, self.settings)
        self.stt = create_stt_provider(self.settings)
        self.tts = create_tts_provider(self.settings)

    def _actions(self) -> list[Action]:
        return ActionRepository(self.db).list_all()

    def _resolve_proyect_id(
        self, user: Optional[User], requested: int | None = None
    ) -> int:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Conversation has no user",
            )
        repo = ProyectsRepository(self.db)
        if requested is not None:
            try:
                row = repo.get_active_for_user(user.id, int(requested))
            except (TypeError, ValueError):
                row = None
            if row is not None:
                return int(row.id)
        first = repo.list_for_user(user.id, limit=1)
        if first:
            return int(first[0].id)
        return int(repo.ensure_default_for_user(user.id).id)

    async def create(self, user: User, payload: ConversationCreate) -> ConversationOut:
        if not user.character_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User has no character assigned",
            )

        character = self.characters.get_by_id(user.character_id)
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")

        conversation = Conversation(
            id=f"conv_{uuid.uuid4().hex[:10]}",
            user_id=user.id,
            character_id=character.id,
            status="active",
            device_id=payload.device_id,
        )
        saved = self.conversations.create(conversation)
        self.metrics.track(
            MetricEvent(
                event="conversation_started",
                user_id=user.id,
                character_id=character.id,
                conversation_id=saved.id,
                device_id=payload.device_id,
            )
        )

        intro_text = self._build_intro(user)
        audio_content = await self.tts.synthesize(intro_text, character.voice_id)
        extension = "wav" if self.settings.tts_provider == "mock" else "mp3"
        audio_url = await self.audio.save_audio(audio_content, extension=extension)

        intro_message = self.conversations.add_message(
            ConversationMessage(
                conversation_id=saved.id,
                proyect_id=self._resolve_proyect_id(user, payload.proyect_id),
                role="assistant",
                text=intro_text,
                audio_url=audio_url,
            )
        )

        return ConversationOut(
            conversation_id=saved.id,
            user_id=saved.user_id,
            character_id=saved.character_id,
            status=saved.status,
            messages=[MessageOut.model_validate(intro_message)],
        )

    @staticmethod
    def _build_intro(user: User) -> str:
        alias = (user.alias or "").strip() or "usuario"
        return f"hola {alias}"

    def get(self, conversation_id: str) -> ConversationOut:
        conversation = self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = self.conversations.list_messages(conversation.id)
        return ConversationOut(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            character_id=conversation.character_id,
            status=conversation.status,
            messages=[MessageOut.model_validate(m) for m in messages],
        )

    async def process_audio(
        self,
        conversation_id: str,
        audio_bytes: bytes,
        proyect_id: int | None = None,
    ) -> AudioConversationResponse:
        conversation = self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        character = self.characters.get_by_id(conversation.character_id)
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")

        user = self.users.get_by_id(conversation.user_id)
        latency: dict[str, Any] = {}

        t0 = time.perf_counter()
        user_text = await self.stt.transcribe(
            audio_bytes, language=self.settings.stt_language
        )
        if not user_text:
            logger.warning(
                "STT empty, retry once conversation=%s bytes=%s",
                conversation_id,
                len(audio_bytes),
            )
            user_text = await self.stt.transcribe(
                audio_bytes, language=self.settings.stt_language
            )
        latency["stt_ms"] = int((time.perf_counter() - t0) * 1000)
        if not user_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not transcribe audio",
            )

        return await self._process_user_turn(
            conversation=conversation,
            character=character,
            user=user,
            user_text=user_text,
            latency=latency,
            proyect_id=proyect_id,
        )

    async def process_text(
        self,
        conversation_id: str,
        text: str,
        proyect_id: int | None = None,
    ) -> AudioConversationResponse:
        conversation = self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        character = self.characters.get_by_id(conversation.character_id)
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")

        user = self.users.get_by_id(conversation.user_id)
        user_text = (text or "").strip()
        if not user_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Empty text",
            )
        return await self._process_user_turn(
            conversation=conversation,
            character=character,
            user=user,
            user_text=user_text,
            latency={},
            proyect_id=proyect_id,
        )

    async def _process_user_turn(
        self,
        *,
        conversation: Conversation,
        character: Character,
        user: Optional[User],
        user_text: str,
        latency: dict[str, Any],
        proyect_id: int | None = None,
    ) -> AudioConversationResponse:
        resolved_proyect_id = self._resolve_proyect_id(user, proyect_id)
        self.conversations.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                proyect_id=resolved_proyect_id,
                role="user",
                text=user_text,
            )
        )

        if user is not None:
            db_messages = self.conversations.list_messages_for_user_proyect(
                int(user.id),
                resolved_proyect_id,
                since=memory_cutoff(user),
                limit=LLM_HISTORY_MAX_MESSAGES,
            )
        else:
            db_messages = self.conversations.list_messages(
                conversation.id, proyect_id=resolved_proyect_id
            )
        history = [
            {"role": m.role, "content": m.text}
            for m in db_messages
            if m.role in ("user", "assistant") and (m.text or "").strip()
        ]
        logger.info(
            "LLM history conversation=%s messages=%s roles=%s",
            conversation.id,
            len(history),
            [m["role"] for m in history],
        )

        t1 = time.perf_counter()
        action_result = None
        assistant_text = ""
        selected_proyect = str(resolved_proyect_id)
        proyect_name = None
        if user is not None:
            selected_row = ProyectsRepository(self.db).get_active_for_user(
                int(user.id), resolved_proyect_id
            )
            proyect_name = selected_row.name if selected_row is not None else None

        catalog = self._actions()
        decision = await self.intent.classify(
            user_text,
            actions=catalog,
            user=user,
            db=self.db,
            proyect_id=resolved_proyect_id,
            proyect_name=proyect_name,
            pending=pending_intent_context(user),
            history=history,
        )
        latency["intent_ms"] = int((time.perf_counter() - t1) * 1000)
        logger.info(
            "Turn text=%r proyect=%s name=%r intent=%s source=%s valor=%r",
            user_text,
            selected_proyect,
            proyect_name,
            decision.clave,
            decision.source,
            decision.valor,
        )

        if decision.is_action:
            valor = decision.valor
            valor1 = decision.valor1
            if decision.clave in (
                "agregar_tarea",
                "listar_tareas",
                "eliminar_tarea",
                "eliminar_mensajes",
            ):
                valor1 = selected_proyect
            if decision.clave in (
                "agregar_recordatorio",
                "listar_recordatorios",
                "eliminar_recordatorio",
                "eliminar_proyecto",
            ):
                valor1 = ""
            if decision.clave == "listar_tareas":
                valor = selected_proyect
            if decision.clave == "agregar_tarea" and not valor:
                ext_name, _ext_proj = extract_add_task_parts(user_text)
                valor = ext_name
            if decision.clave == "eliminar_tarea" and not valor:
                valor = extract_delete_task_name(user_text)
            if decision.clave == "agregar_proyecto" and not valor:
                valor = extract_add_project_name(user_text)
            if decision.clave == "seleccionar_proyecto" and not valor:
                valor = extract_select_project_name(user_text)
            if decision.clave == "eliminar_proyecto" and not valor:
                valor = extract_delete_project_name(user_text)
            if decision.clave == "agregar_recordatorio" and not valor:
                valor = user_text
            action_result = await ActionDispatcher(self.db, self.settings).dispatch(
                clave=decision.clave,
                valor=valor,
                user=user,
                user_text=user_text,
                fecha=decision.fecha or None,
                confirm=decision.confirm,
                valor1=valor1,
                recurrencia=getattr(decision, "recurrencia", None) or None,
                dias=getattr(decision, "dias", None) or None,
            )
            assistant_text = (
                (action_result.spoken_override if action_result else None) or ""
            )
            latency["llm_ms"] = 0
            if action_result is None:
                decision = IntentDecision(clave="none", source=decision.source)

        if not decision.is_action or action_result is None:
            t_llm = time.perf_counter()
            profile = self.users.get_profile_text(user.id) if user is not None else ""
            system_prompt = build_llm_system_prompt(
                character, user_profile=profile
            )
            raw_assistant = await self.llm.generate(history, system_prompt)
            latency["llm_ms"] = int((time.perf_counter() - t_llm) * 1000)
            assistant_text, action_result = await run_llm_action(
                self.db,
                self.settings,
                raw_assistant_text=raw_assistant,
                user=user,
                user_text=user_text,
                proyect_id=resolved_proyect_id,
            )

        extra = action_result.extra if action_result else None
        tts_text = assistant_text
        if extra and extra.get("display"):
            assistant_text = str(extra["display"])
        if not (tts_text or "").strip():
            tts_text = assistant_text or "Dale."
        if not (assistant_text or "").strip():
            assistant_text = tts_text

        items = []
        items_title = None
        proyect_id = None
        reload_chat = bool(extra and extra.get("reload_chat"))
        if extra and isinstance(extra.get("items"), list):
            items = extra["items"]
        if extra and extra.get("items_title"):
            items_title = str(extra["items_title"])
        if (
            action_result
            and extra
            and extra.get("proyect_id") is not None
            and (
                action_result.metodo == "agregar_proyecto"
                or action_result.metodo == "seleccionar_proyecto"
                or (
                    action_result.metodo == "eliminar_proyecto"
                    and extra.get("deleted")
                )
            )
        ):
            try:
                proyect_id = int(extra["proyect_id"])
            except (TypeError, ValueError):
                proyect_id = None

        if action_result:
            logger.info("Action: %s", action_result.to_dict())

        t2 = time.perf_counter()
        extension = "wav" if self.settings.tts_provider == "mock" else "mp3"
        filename = f"response_{uuid.uuid4().hex[:12]}.{extension}"
        audio_path = self.audio.audio_dir / filename
        audio_url = f"/static/audio/{filename}"
        iterator = self.tts.iter_audio(tts_text, character.voice_id).__aiter__()
        try:
            first = await anext(iterator)
        except StopAsyncIteration:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="TTS returned empty audio",
            ) from None
        ttfb_ms = int((time.perf_counter() - t2) * 1000)
        latency["tts_ttfb_ms"] = ttfb_ms
        latency["tts_ms"] = ttfb_ms
        latency["total_ms"] = sum(
            v for v in latency.values() if isinstance(v, int)
        )

        stream = open_tts_stream()
        await stream.push(first)
        stream_url = (
            f"/api/v1/conversations/{conversation.id}/tts/{stream.token}.{extension}"
        )
        action_payload = action_result.to_dict() if action_result else None
        task = asyncio.create_task(
            self._finish_tts_stream(
                iterator=iterator,
                first=first,
                stream=stream,
                audio_path=audio_path,
                audio_url=audio_url,
                conversation_id=conversation.id,
                user_id=conversation.user_id,
                character_id=conversation.character_id,
                assistant_text=assistant_text,
                latency=dict(latency),
                action_payload=action_payload,
                started=t2,
                proyect_id=resolved_proyect_id,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info(
            "conversation=%s stt=%sms llm=%sms tts=%sms primer byte",
            conversation.id,
            latency.get("stt_ms"),
            latency.get("llm_ms"),
            ttfb_ms,
        )

        return AudioConversationResponse(
            conversation_id=conversation.id,
            user_text=user_text,
            assistant_text=assistant_text,
            audio_url=audio_url,
            stream_url=stream_url,
            character_state="talking",
            latency=latency,
            items=items,
            items_title=items_title,
            proyect_id=proyect_id,
            reload_chat=reload_chat,
        )

    async def _finish_tts_stream(
        self,
        *,
        iterator,
        first: bytes,
        stream: TtsByteStream,
        audio_path,
        audio_url: str,
        conversation_id: str,
        user_id,
        character_id: str,
        assistant_text: str,
        latency: dict[str, Any],
        action_payload: dict[str, Any] | None,
        started: float,
        proyect_id: int,
    ) -> None:
        """Sigue el TTS, lo guarda y cierra el stream que ya está sonando."""
        import aiofiles

        parts = [first]
        try:
            async with aiofiles.open(audio_path, "wb") as handle:
                await handle.write(first)
                async for chunk in iterator:
                    if not chunk:
                        continue
                    parts.append(chunk)
                    await handle.write(chunk)
                    await stream.push(chunk)
        except Exception:
            logger.exception("TTS stream conversation=%s", conversation_id)
        finally:
            await stream.finish()

        total_ms = int((time.perf_counter() - started) * 1000)
        latency["tts_ms"] = total_ms
        latency["tts_total_ms"] = total_ms
        logger.info(
            "conversation=%s tts=%sms primer byte (total %sms)",
            conversation_id,
            latency.get("tts_ttfb_ms"),
            total_ms,
        )
        if len(parts) == 1 and not first:
            return

        db = SessionLocal()
        try:
            ConversationRepository(db).add_message(
                ConversationMessage(
                    conversation_id=conversation_id,
                    proyect_id=proyect_id,
                    role="assistant",
                    text=assistant_text,
                    audio_url=audio_url,
                    latency_ms=latency,
                )
            )
            MetricsService(db).track(
                MetricEvent(
                    event="conversation_message",
                    user_id=user_id,
                    character_id=character_id,
                    conversation_id=conversation_id,
                    metadata={**latency, "action": action_payload},
                )
            )
        except Exception:
            logger.exception("No se pudo guardar el TTS conversation=%s", conversation_id)
        finally:
            db.close()

    def _tts_extension(self) -> str:
        if self.settings.tts_provider == "mock":
            return "wav"
        return "mp3"

    def _ms(self, value: object) -> str:
        if value is None:
            return "-"
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return "-"

    async def start_conversation(
        self,
        user: User,
        character_id: str | None = None,
        device_id: str | None = None,
        player=None,
    ) -> tuple[Conversation, bytes, str]:
        """Flujo local del bot: saludo TTS y conversación activa."""
        from app.bot.playback import consume_tts

        cid = (character_id or user.character_id or "").strip()
        if not cid:
            raise RuntimeError(
                f"El usuario {user.username!r} no tiene character_id en la base"
            )
        character = self.characters.get_by_id(cid)
        if not character:
            raise RuntimeError(
                f"Character {cid!r} no existe (user={user.username!r})"
            )

        conversation = Conversation(
            id=f"conv_{uuid.uuid4().hex[:10]}",
            user_id=user.id,
            character_id=character.id,
            status="active",
            device_id=device_id,
        )
        saved = self.conversations.create(conversation)

        intro_latency: dict[str, int] = {}
        if self.settings.bot_opening_mode == "llm":
            system_prompt = build_bot_llm_system_prompt(
                character,
                user,
                self.settings.resolved_bot_system_prompt,
                self._actions(),
            )
            t_llm = time.perf_counter()
            intro_text = await self.llm.generate(
                [{"role": "user", "content": "Saludame brevemente para empezar."}],
                system_prompt,
            )
            intro_latency["llm_ms"] = int((time.perf_counter() - t_llm) * 1000)
        else:
            name = (user.full_name or "").strip()
            greeting = self.settings.bot_greeting.strip() or "Hola"
            intro_text = f"{greeting}{', ' + name if name else ''}. ¿En qué te ayudo?"

        audio_content, ttfb_ms, tts_ms = await consume_tts(
            self.tts, intro_text, character.voice_id, player
        )
        intro_latency["tts_ttfb_ms"] = ttfb_ms
        intro_latency["tts_ms"] = tts_ms
        logger.info(
            "Tiempos STT=- LLM=%s TTS=%s primer byte (total %s) (intro)",
            self._ms(intro_latency.get("llm_ms")),
            self._ms(ttfb_ms),
            self._ms(tts_ms),
        )
        audio_path = await self.audio.save_audio(
            audio_content, extension=self._tts_extension()
        )
        proyect_id = None
        try:
            from app.bot.proyect_context import resolve_proyect_id_for_user

            proyect_id = resolve_proyect_id_for_user(self.db, user)
        except Exception:
            logger.debug("Sin proyecto default al iniciar conversación bot", exc_info=True)
        self.conversations.add_message(
            ConversationMessage(
                conversation_id=saved.id,
                proyect_id=proyect_id,
                role="assistant",
                text=intro_text,
                audio_url=audio_path,
            )
        )
        return saved, audio_content, intro_text

    async def process_audio_local(
        self,
        conversation_id: str,
        audio_bytes: bytes,
        *,
        user_text: str | None = None,
        stt_ms: int | None = None,
        player=None,
        proyect_id: int | None = None,
    ) -> dict[str, Any]:
        """Flujo local del bot: STT/LLM/acciones + audio completo en memoria."""
        from app.bot.playback import consume_tts
        from app.bot.proyect_context import resolve_proyect_id_for_user

        conversation = self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise RuntimeError(f"Conversation not found: {conversation_id}")

        character = self.characters.get_by_id(conversation.character_id)
        if not character:
            raise RuntimeError(f"Character not found: {conversation.character_id}")

        user = self.users.get_by_id(conversation.user_id)
        latency: dict[str, Any] = {}

        if user_text is None:
            t0 = time.perf_counter()
            user_text = await self.stt.transcribe(
                audio_bytes, language=self.settings.stt_language
            )
            latency["stt_ms"] = int((time.perf_counter() - t0) * 1000)
        else:
            latency["stt_ms"] = stt_ms or 0
            latency["stt_stream"] = True
        if not user_text:
            logger.info(
                "Tiempos STT=%s LLM=- TTS=-",
                self._ms(latency.get("stt_ms")),
            )
            return {
                "conversation_id": conversation.id,
                "user_text": "",
                "assistant_text": "",
                "audio_bytes": b"",
                "audio_path": None,
                "latency": latency,
                "action": None,
            }

        try:
            if proyect_id is not None:
                proyect_id = self._resolve_proyect_id(user, proyect_id)
            else:
                proyect_id = resolve_proyect_id_for_user(self.db, user)
        except Exception:
            logger.debug("Sin proyecto en turno bot", exc_info=True)
            proyect_id = None

        self.conversations.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                proyect_id=proyect_id,
                role="user",
                text=user_text,
            )
        )

        history = [
            {"role": m.role, "content": m.text}
            for m in self.conversations.get_by_id(conversation.id).messages  # type: ignore[union-attr]
        ]
        system_prompt = build_bot_llm_system_prompt(
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
            proyect_id=proyect_id,
        )
        if not assistant_text.strip():
            assistant_text = "Dale."

        logger.info("User: %s", user_text)
        logger.info("TORI: %s", assistant_text)
        audio_content, ttfb_ms, tts_ms = await consume_tts(
            self.tts, assistant_text, character.voice_id, player
        )
        latency["tts_ttfb_ms"] = ttfb_ms
        latency["tts_ms"] = tts_ms
        latency["total_ms"] = (
            int(latency.get("stt_ms") or 0)
            + int(latency.get("llm_ms") or 0)
            + tts_ms
        )

        audio_path = await self.audio.save_audio(
            audio_content, extension=self._tts_extension()
        )
        self.conversations.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                proyect_id=proyect_id,
                role="assistant",
                text=assistant_text,
                audio_url=audio_path,
                latency_ms=latency,
            )
        )

        logger.info(
            "Tiempos STT=%s%s LLM=%s TTS=%s primer byte (total %s)",
            self._ms(latency.get("stt_ms")),
            " stream" if latency.get("stt_stream") else "",
            self._ms(latency.get("llm_ms")),
            self._ms(latency.get("tts_ttfb_ms")),
            self._ms(latency.get("tts_ms")),
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

    def end_conversation(self, conversation_id: str) -> None:
        self.conversations.update_status(conversation_id, "ended")
