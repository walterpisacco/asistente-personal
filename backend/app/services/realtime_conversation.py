"""Conversación speech-to-speech con OpenAI Realtime + acciones locales."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.actions import Action
from app.models.character import Character
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.user import User
from app.bot.proyect_context import resolve_proyect_id_for_user
from app.providers.realtime.openai_realtime import OpenAIRealtimeConnection
from app.repositories.action_repository import ActionRepository
from app.repositories.character_repository import CharacterRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.user_repository import UserRepository
from app.services.actions import ActionDispatcher
from app.services.call_metrics_service import CallMetricsTracker
from app.services.conversation_service import build_bot_llm_system_prompt

logger = logging.getLogger(__name__)


def resample_pcm16(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Re-samplea PCM int16 mono con interpolación lineal."""
    if src_rate == dst_rate or pcm.size == 0:
        return pcm.astype(np.int16, copy=False)
    duration = len(pcm) / float(src_rate)
    n_out = max(1, int(round(duration * dst_rate)))
    x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    out = np.interp(x_new, x_old, pcm.astype(np.float32))
    return np.clip(out, -32768, 32767).astype(np.int16)


def _action_tools(actions: list[Action]) -> list[dict[str, Any]]:
    claves = sorted({(a.clave or "").strip() for a in actions if (a.clave or "").strip()})
    if not claves:
        claves = ["finalizar"]
    return [
        {
            "type": "function",
            "name": "run_action",
            "description": (
                "Ejecutá una acción del backend del asistente (recordatorios, "
                "proyectos, tareas, música/video, finalizar conversación). "
                "Usá la clave exacta del catálogo cuando el usuario lo pida."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clave": {
                        "type": "string",
                        "description": "Clave de la acción",
                        "enum": claves,
                    },
                    "valor": {
                        "type": "string",
                        "description": "Valor principal (texto, nombre, query de música, etc.)",
                    },
                    "valor1": {
                        "type": "string",
                        "description": "Valor secundario opcional",
                    },
                    "fecha": {
                        "type": "string",
                        "description": "Fecha/hora si aplica (YYYY-MM-DD HH:MM o HH:MM)",
                    },
                    "recurrencia": {
                        "type": "string",
                        "description": "once|daily|weekly|weekends",
                    },
                    "dias": {
                        "type": "string",
                        "description": "Días para recurrencia weekly",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Confirmación de borrado u operación pendiente",
                    },
                },
                "required": ["clave"],
            },
        }
    ]


@dataclass
class RealtimeTurnResult:
    conversation_id: str
    ended: bool = False
    user_transcript: str = ""
    assistant_transcript: str = ""
    action: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class RealtimeVoiceSession:
    """Una sesión Realtime por conversación de voz del bot."""

    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.conversations = ConversationRepository(db)
        self.characters = CharacterRepository(db)
        self.users = UserRepository(db)
        self._conn: OpenAIRealtimeConnection | None = None
        self._conversation: Conversation | None = None
        self._character: Character | None = None
        self._user: User | None = None
        self._proyect_id: int | None = None
        self._playing = False
        self._ended = False
        self._last_speech_at = time.monotonic()
        self._user_transcript = ""
        self._assistant_transcript = ""
        self._pending_action: dict[str, Any] | None = None
        self._audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._events_task: asyncio.Task | None = None
        self._metrics: CallMetricsTracker | None = None
        self._disconnect_reason: str = "unknown"
        self._greeting_pending = True
        self._response_started_mono: float | None = None

    @property
    def conversation(self) -> Conversation | None:
        return self._conversation

    @property
    def character(self) -> Character | None:
        return self._character

    @property
    def ended(self) -> bool:
        return self._ended

    async def start(self, user: User, *, device_id: str | None = None) -> Conversation:
        cid = (user.character_id or "").strip()
        if not cid:
            raise RuntimeError(f"El usuario {user.username!r} no tiene character_id")
        character = self.characters.get_by_id(cid)
        if not character:
            raise RuntimeError(f"Character {cid!r} no existe")

        conversation = Conversation(
            id=f"conv_{__import__('uuid').uuid4().hex[:10]}",
            user_id=user.id,
            character_id=character.id,
            status="active",
            device_id=device_id,
        )
        saved = self.conversations.create(conversation)
        self._conversation = saved
        self._character = character
        self._user = user
        self._proyect_id = resolve_proyect_id_for_user(self.db, user)
        logger.info("Realtime proyect_id=%s", self._proyect_id)

        actions = ActionRepository(self.db).list_all()
        instructions = build_bot_llm_system_prompt(
            character,
            user,
            self.settings.resolved_bot_system_prompt,
            actions,
        )
        instructions += (
            "\n\nHERRAMIENTAS:\n"
            "- Para ejecutar acciones del backend usá la función run_action.\n"
            "- Si el usuario pide seleccionar/elegir/cambiar/pasar/activar un proyecto "
            "(ej. 'seleccioná el proyecto cocina'), usá clave=seleccionar_proyecto y "
            "valor=nombre del proyecto. Eso cambia el proyecto activo en la UI.\n"
            "- No inventes JSON al final del texto; preferí la herramienta.\n"
            "- Respondé solo con voz natural, frases cortas, sin markdown."
        )

        model = (self.settings.openai_realtime_model or "gpt-realtime-2.1").strip()
        voice = (self.settings.openai_realtime_voice or "marin").strip()
        tx_model = (
            self.settings.openai_realtime_transcription_model
            or "gpt-4o-mini-transcribe"
        ).strip()
        rate = int(self.settings.openai_realtime_sample_rate or 24000)
        self._metrics = CallMetricsTracker.start(
            saved.id,
            self.settings,
            mode="realtime",
            providers={
                "conversation": "openai_realtime",
                "model": model,
                "transcription_model": tx_model,
                "voice": voice,
            },
        )

        self._conn = OpenAIRealtimeConnection(
            api_key=self.settings.openai_api_key,
            model=model,
            safety_identifier=str(user.id),
        )
        await self._conn.connect()
        await self._conn.update_session(
            {
                "type": "realtime",
                "model": model,
                "instructions": instructions,
                "output_modalities": ["audio"],
                "tools": _action_tools(actions),
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": rate},
                        "transcription": {
                            "model": tx_model,
                            "language": "es",
                        },
                        # Half-duplex local: el mic se cierra mientras habla el bot,
                        # así no hace falta barge-in (evita eco parlante→mic).
                        "turn_detection": {
                            "type": "semantic_vad",
                            "interrupt_response": False,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": rate},
                        "voice": voice,
                    },
                },
            }
        )
        self._session_ready = asyncio.Event()
        self._events_task = asyncio.create_task(
            self._consume_events(), name="realtime-events"
        )
        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning("Realtime session.updated no llegó a tiempo; sigo igual")
        # Saludo inicial del modelo.
        self._greeting_pending = True
        self._response_started_mono = time.monotonic()
        await self._conn.create_response()
        self._last_speech_at = time.monotonic()
        return saved

    async def _consume_events(self) -> None:
        assert self._conn is not None
        try:
            async for event in self._conn.events():
                await self._handle_event(event)
                if self._ended:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Realtime event loop error")
            self._ended = True
        finally:
            await self._audio_q.put(None)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        et = event.get("type") or ""
        if et in ("session.updated", "session.created"):
            self._session_ready.set()
            self._last_speech_at = time.monotonic()
            if self._metrics is not None:
                self._metrics.mark_answered()
        elif et in ("response.output_audio.delta", "response.audio.delta"):
            delta = event.get("delta") or ""
            if delta:
                if self._metrics is not None:
                    self._metrics.mark_first_bot_audio()
                self._playing = True
                self._last_speech_at = time.monotonic()
                await self._audio_q.put(base64.b64decode(delta))
        elif et in (
            "response.output_audio.done",
            "response.audio.done",
            "response.done",
        ):
            if et == "response.done":
                self._ingest_response_usage(event)
                await self._maybe_handle_function_calls(event)
            self._playing = False
            self._last_speech_at = time.monotonic()
            # Señal de fin de chunk de audio para el player (no cierra la cola).
            await self._audio_q.put(b"")
        elif et in (
            "response.output_audio_transcript.delta",
            "response.audio_transcript.delta",
        ):
            self._assistant_transcript += event.get("delta") or ""
        elif et in (
            "response.output_audio_transcript.done",
            "response.audio_transcript.done",
        ):
            text = (event.get("transcript") or self._assistant_transcript or "").strip()
            if text:
                self._assistant_transcript = text
                self._persist_message("assistant", text)
                logger.info("TORI (realtime): %s", text)
                if self._metrics is not None:
                    llm_ms = None
                    if self._response_started_mono is not None:
                        llm_ms = int(
                            (time.monotonic() - self._response_started_mono) * 1000
                        )
                    self._metrics.mark_bot_turn(text=text, llm_ms=llm_ms)
                    self._greeting_pending = False
                    self._response_started_mono = None
        elif et == "conversation.item.input_audio_transcription.completed":
            text = (event.get("transcript") or "").strip()
            if text:
                self._user_transcript = text
                self._persist_message("user", text)
                logger.info("User (realtime): %s", text)
                if self._metrics is not None:
                    self._metrics.mark_user_turn(text=text)
                self._response_started_mono = time.monotonic()
        elif et == "input_audio_buffer.speech_started":
            self._last_speech_at = time.monotonic()
            if self._metrics is not None:
                self._metrics.mark_speech_started()
            # Si el mic filtró mal y llegó eco, no interrumpimos la reproducción local.
        elif et == "input_audio_buffer.speech_stopped":
            self._last_speech_at = time.monotonic()
            if self._metrics is not None:
                self._metrics.mark_speech_stopped()
        elif et == "error":
            logger.error("Realtime event error: %s", event.get("error") or event)
            self._disconnect_reason = "error"

    def _ingest_response_usage(self, event: dict[str, Any]) -> None:
        if self._metrics is None:
            return
        response = event.get("response") or {}
        usage = response.get("usage") or event.get("usage") or {}
        if not isinstance(usage, dict):
            return
        prompt = (
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("total_tokens")
            or 0
        )
        completion = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or 0
        )
        # En Realtime a veces viene desglose input/output dentro de usage.
        details_in = usage.get("input_token_details") or {}
        details_out = usage.get("output_token_details") or {}
        if isinstance(details_in, dict) and not usage.get("input_tokens"):
            prompt = int(details_in.get("text_tokens") or 0) + int(
                details_in.get("audio_tokens") or 0
            )
        if isinstance(details_out, dict) and not usage.get("output_tokens"):
            completion = int(details_out.get("text_tokens") or 0) + int(
                details_out.get("audio_tokens") or 0
            )
        try:
            self._metrics.add_usage(
                input_tokens=int(prompt or 0),
                output_tokens=int(completion or 0),
            )
        except (TypeError, ValueError):
            pass
        self._metrics.mark_llm_request()
    async def _maybe_handle_function_calls(self, event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        outputs = response.get("output") or []
        if not isinstance(outputs, list):
            return
        handled = False
        for item in outputs:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            name = item.get("name") or ""
            call_id = item.get("call_id") or ""
            raw_args = item.get("arguments") or "{}"
            if name != "run_action" or not call_id:
                continue
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            result = await self._dispatch_action(args if isinstance(args, dict) else {})
            handled = True
            assert self._conn is not None
            await self._conn.create_function_output(
                call_id, json.dumps(result, ensure_ascii=False)
            )
        if handled and self._conn is not None and not self._ended:
            await self._conn.create_response()

    async def _dispatch_action(self, args: dict[str, Any]) -> dict[str, Any]:
        clave = str(args.get("clave") or "").strip()
        if not clave:
            return {"ok": False, "error": "clave vacía"}
        confirm = args.get("confirm")
        if confirm is not None and not isinstance(confirm, bool):
            confirm = str(confirm).lower() in ("1", "true", "yes", "si", "sí")
        valor1 = str(args.get("valor1") or "").strip() or None
        # Si la UI tiene un proyecto seleccionado, úsalo como en mobile (proyect_id).
        selected = self._current_proyect_id()
        if selected is not None and not valor1:
            taskish = clave in {
                "listar_tareas",
                "agregar_tarea",
                "eliminar_tarea",
                "eliminar_tareas",
                "eliminar_mensajes",
                "eliminar_mensaje",
            }
            if taskish:
                valor1 = str(selected)
        action_result = await ActionDispatcher(self.db, self.settings).dispatch(
            clave=clave,
            valor=str(args.get("valor") or ""),
            user=self._user,
            user_text=self._user_transcript or "",
            fecha=(str(args.get("fecha")).strip() if args.get("fecha") else None),
            confirm=confirm,
            valor1=valor1,
            recurrencia=str(args.get("recurrencia") or "") or None,
            dias=str(args.get("dias") or "") or None,
        )
        if action_result is None:
            return {"ok": False, "error": "acción desconocida", "clave": clave}
        payload = action_result.to_dict()
        self._pending_action = payload
        if payload.get("end_session"):
            self._ended = True
            self._disconnect_reason = "finalizar"
        spoken = (action_result.spoken_override or "").strip()
        return {
            "ok": True,
            "clave": clave,
            "metodo": action_result.metodo,
            "spoken": spoken,
            "extra": action_result.extra or {},
            "end_session": bool(payload.get("end_session")),
        }

    def _current_proyect_id(self) -> int | None:
        """Relee la selección de la UI (puede cambiar durante la charla)."""
        self._proyect_id = resolve_proyect_id_for_user(self.db, self._user)
        return self._proyect_id

    def _persist_message(self, role: str, text: str) -> None:
        if self._conversation is None or not text.strip():
            return
        try:
            self.conversations.add_message(
                ConversationMessage(
                    conversation_id=self._conversation.id,
                    proyect_id=self._current_proyect_id(),
                    role=role,
                    text=text.strip(),
                )
            )
        except Exception:
            logger.exception("No se pudo guardar mensaje realtime role=%s", role)

    @property
    def is_playing(self) -> bool:
        """True mientras el modelo está emitiendo audio (no mandar mic)."""
        return self._playing

    async def send_mic_pcm(self, pcm16: np.ndarray, *, src_rate: int) -> None:
        if self._conn is None or self._ended or pcm16.size == 0 or self._playing:
            return
        dst = int(self.settings.openai_realtime_sample_rate or 24000)
        pcm = resample_pcm16(pcm16, src_rate, dst)
        await self._conn.append_audio(pcm.tobytes())

    async def clear_input(self) -> None:
        """Descarta eco ya bufferizado en el servidor."""
        if self._conn is None or self._ended:
            return
        try:
            await self._conn.clear_input_audio()
        except Exception:
            logger.debug("clear_input_audio falló", exc_info=True)

    async def audio_chunks(self) -> Any:
        """Async iterator de chunks PCM (bytes). b'' = fin de respuesta; None = fin sesión."""
        while True:
            chunk = await self._audio_q.get()
            yield chunk
            if chunk is None:
                break

    def idle_timed_out(self, end_call_ms: int) -> bool:
        if self._playing:
            return False
        idle_ms = (time.monotonic() - self._last_speech_at) * 1000.0
        return idle_ms >= max(1000, int(end_call_ms))

    def set_disconnect_reason(self, reason: str) -> None:
        if reason:
            self._disconnect_reason = reason

    def flush_metrics(self, *, disconnect_reason: str | None = None) -> None:
        if self._metrics is None:
            return
        reason = disconnect_reason or self._disconnect_reason or "unknown"
        self._metrics.flush(self.db, disconnect_reason=reason)

    def end(self, *, disconnect_reason: str | None = None) -> None:
        self._ended = True
        if disconnect_reason:
            self._disconnect_reason = disconnect_reason
        if self._conversation is not None:
            try:
                self.conversations.update_status(self._conversation.id, "ended")
            except Exception:
                logger.exception("No se pudo marcar conversación ended")
        self.flush_metrics()

    async def close(self) -> None:
        self._ended = True
        # Si end() no corrió (error temprano), igual persistimos métricas.
        self.flush_metrics()
        if self._events_task is not None:
            self._events_task.cancel()
            try:
                await self._events_task
            except asyncio.CancelledError:
                pass
            self._events_task = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        await self._audio_q.put(None)

    def take_action(self) -> dict[str, Any] | None:
        action = self._pending_action
        self._pending_action = None
        return action
