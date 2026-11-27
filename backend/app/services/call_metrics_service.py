"""Métricas de una llamada: acumula en memoria y hace 1 INSERT al colgar."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.call_sessions import CallSessions
from app.repositories.call_sessions_repository import CallSessionsRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _avg(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


@dataclass
class CallMetricsTracker:
    """Tracker por conversación. Thread/async-safe a nivel de un solo dueño."""

    conversation_id: str
    settings: Settings
    mode: str = "realtime"
    enabled: bool = True
    start_time: datetime = field(default_factory=_utc_now)
    answer_time: Optional[datetime] = None
    first_bot_audio_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    time_to_first_audio_ms: Optional[int] = None
    user_turns: int = 0
    bot_turns: int = 0
    stt_requests: int = 0
    llm_requests: int = 0
    tts_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    # Desglose Realtime (audio vs text); en pipeline quedan en 0.
    audio_input_tokens: int = 0
    audio_output_tokens: int = 0
    text_input_tokens: int = 0
    text_output_tokens: int = 0
    audio_cached_input_tokens: int = 0
    text_cached_input_tokens: int = 0
    stt_audio_seconds: float = 0.0
    tts_characters: int = 0
    interruptions: int = 0
    silence_seconds: float = 0.0
    disconnect_reason: str = "unknown"
    providers: dict[str, Any] = field(default_factory=dict)
    turn_timings: list[dict[str, Any]] = field(default_factory=list)
    last_cost_breakdown: dict[str, Any] = field(default_factory=dict)
    _llm_latencies: list[float] = field(default_factory=list)
    _tts_latencies: list[float] = field(default_factory=list)
    _stt_latencies: list[float] = field(default_factory=list)
    _response_latencies: list[float] = field(default_factory=list)
    _mono_start: float = field(default_factory=time.monotonic)
    _speech_started_mono: Optional[float] = None
    _idle_since_mono: Optional[float] = None
    _turn_user_started_mono: Optional[float] = None
    _pending_response_started_mono: Optional[float] = None
    _flushed: bool = False

    @classmethod
    def start(
        cls,
        conversation_id: str,
        settings: Settings | None = None,
        *,
        mode: str = "realtime",
        providers: dict[str, Any] | None = None,
    ) -> "CallMetricsTracker":
        cfg = settings or get_settings()
        tracker = cls(
            conversation_id=conversation_id,
            settings=cfg,
            mode=mode,
            enabled=bool(getattr(cfg, "call_metrics_enabled", True)),
            providers=providers or {},
        )
        tracker.answer_time = tracker.start_time
        tracker._idle_since_mono = tracker._mono_start
        return tracker

    def mark_answered(self) -> None:
        if self.answer_time is None:
            self.answer_time = _utc_now()

    def mark_first_bot_audio(self) -> None:
        if self.first_bot_audio_time is not None:
            return
        now = _utc_now()
        self.first_bot_audio_time = now
        self.time_to_first_audio_ms = int(
            (now - self.start_time).total_seconds() * 1000
        )
        self._close_idle()

    def mark_speech_started(self) -> None:
        self._close_idle()
        self._speech_started_mono = time.monotonic()
        self._turn_user_started_mono = self._speech_started_mono

    def mark_speech_stopped(self) -> None:
        now = time.monotonic()
        if self._speech_started_mono is not None:
            self.stt_audio_seconds += max(0.0, now - self._speech_started_mono)
            self._speech_started_mono = None
        self._idle_since_mono = now

    def mark_user_turn(self, *, text: str = "", stt_ms: int | None = None) -> None:
        self.user_turns += 1
        self.stt_requests += 1
        if stt_ms is not None:
            self._stt_latencies.append(float(stt_ms))
        self._pending_response_started_mono = time.monotonic()
        self.turn_timings.append(
            {
                "role": "user",
                "at": _utc_now().isoformat(),
                "chars": len(text or ""),
                "stt_ms": stt_ms,
            }
        )

    def mark_bot_turn(
        self,
        *,
        text: str = "",
        tts_ms: int | None = None,
        llm_ms: int | None = None,
    ) -> None:
        self.bot_turns += 1
        self.tts_requests += 1
        chars = len(text or "")
        self.tts_characters += chars
        if tts_ms is not None:
            self._tts_latencies.append(float(tts_ms))
        if llm_ms is not None:
            self._llm_latencies.append(float(llm_ms))
        if self._pending_response_started_mono is not None:
            self._response_latencies.append(
                (time.monotonic() - self._pending_response_started_mono) * 1000.0
            )
            self._pending_response_started_mono = None
        self._close_idle()
        self.turn_timings.append(
            {
                "role": "assistant",
                "at": _utc_now().isoformat(),
                "chars": chars,
                "tts_ms": tts_ms,
                "llm_ms": llm_ms,
            }
        )
        self._idle_since_mono = time.monotonic()

    def mark_llm_request(self, *, latency_ms: int | None = None) -> None:
        self.llm_requests += 1
        if latency_ms is not None:
            self._llm_latencies.append(float(latency_ms))

    def mark_pipeline_turn(
        self,
        *,
        user_text: str = "",
        assistant_text: str = "",
        stt_ms: int | None = None,
        llm_ms: int | None = None,
        tts_ms: int | None = None,
        stt_audio_seconds: float | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Un turno completo del pipeline STT→LLM→TTS."""
        self.user_turns += 1
        self.bot_turns += 1
        self.stt_requests += 1
        self.llm_requests += 1
        self.tts_requests += 1
        self.tts_characters += len(assistant_text or "")
        self.total_prompt_tokens += max(0, int(prompt_tokens or 0))
        self.total_completion_tokens += max(0, int(completion_tokens or 0))
        if stt_audio_seconds:
            self.stt_audio_seconds += float(stt_audio_seconds)
        if stt_ms is not None:
            self._stt_latencies.append(float(stt_ms))
        if llm_ms is not None:
            self._llm_latencies.append(float(llm_ms))
        if tts_ms is not None:
            self._tts_latencies.append(float(tts_ms))
        response_parts = [v for v in (stt_ms, llm_ms, tts_ms) if v is not None]
        if response_parts:
            self._response_latencies.append(float(sum(response_parts)))
        self.turn_timings.append(
            {
                "role": "turn",
                "at": _utc_now().isoformat(),
                "user_chars": len(user_text or ""),
                "assistant_chars": len(assistant_text or ""),
                "stt_ms": stt_ms,
                "llm_ms": llm_ms,
                "tts_ms": tts_ms,
            }
        )

    def add_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        audio_input_tokens: int = 0,
        audio_output_tokens: int = 0,
        text_input_tokens: int = 0,
        text_output_tokens: int = 0,
        audio_cached_input_tokens: int = 0,
        text_cached_input_tokens: int = 0,
    ) -> None:
        audio_in = max(0, int(audio_input_tokens or 0))
        audio_out = max(0, int(audio_output_tokens or 0))
        text_in = max(0, int(text_input_tokens or 0))
        text_out = max(0, int(text_output_tokens or 0))
        audio_cached = max(0, int(audio_cached_input_tokens or 0))
        text_cached = max(0, int(text_cached_input_tokens or 0))

        self.audio_input_tokens += audio_in
        self.audio_output_tokens += audio_out
        self.text_input_tokens += text_in
        self.text_output_tokens += text_out
        self.audio_cached_input_tokens += audio_cached
        self.text_cached_input_tokens += text_cached

        detailed_in = audio_in + text_in + audio_cached + text_cached
        detailed_out = audio_out + text_out
        prompt = int(prompt_tokens or 0) or int(input_tokens or 0) or detailed_in
        completion = int(completion_tokens or 0) or int(output_tokens or 0) or detailed_out
        # Si vino desglose, preferir sumas detalladas para los totales.
        if detailed_in:
            prompt = detailed_in
        if detailed_out:
            completion = detailed_out
        self.total_prompt_tokens += max(0, prompt)
        self.total_completion_tokens += max(0, completion)

    def mark_interruption(self) -> None:
        self.interruptions += 1

    def estimate_cost(self) -> Decimal:
        if (self.mode or "").strip().lower() == "realtime":
            return self._estimate_realtime_cost()
        return self._estimate_pipeline_cost()

    def _estimate_pipeline_cost(self) -> Decimal:
        s = self.settings
        llm_in = (self.total_prompt_tokens / 1_000_000.0) * float(
            s.call_metrics_price_llm_input_per_1m
        )
        llm_out = (self.total_completion_tokens / 1_000_000.0) * float(
            s.call_metrics_price_llm_output_per_1m
        )
        stt = (self.stt_audio_seconds / 60.0) * float(s.call_metrics_price_stt_per_minute)
        tts = (self.tts_characters / 1000.0) * float(
            s.call_metrics_price_tts_per_1k_chars
        )
        total = llm_in + llm_out + stt + tts
        self.last_cost_breakdown = {
            "mode": "pipeline",
            "llm_input": round(llm_in, 6),
            "llm_output": round(llm_out, 6),
            "stt": round(stt, 6),
            "tts": round(tts, 6),
            "total": round(total, 6),
        }
        return Decimal(str(round(total, 6)))

    def _estimate_realtime_cost(self) -> Decimal:
        """Costos OpenAI Realtime (audio/text por 1M) + transcribe opcional."""
        s = self.settings
        per_m = 1_000_000.0
        audio_in = (self.audio_input_tokens / per_m) * float(
            s.call_metrics_price_rt_audio_in_per_1m
        )
        audio_out = (self.audio_output_tokens / per_m) * float(
            s.call_metrics_price_rt_audio_out_per_1m
        )
        text_in = (self.text_input_tokens / per_m) * float(
            s.call_metrics_price_rt_text_in_per_1m
        )
        text_out = (self.text_output_tokens / per_m) * float(
            s.call_metrics_price_rt_text_out_per_1m
        )
        audio_cached = (self.audio_cached_input_tokens / per_m) * float(
            s.call_metrics_price_rt_audio_cached_in_per_1m
        )
        text_cached = (self.text_cached_input_tokens / per_m) * float(
            s.call_metrics_price_rt_text_cached_in_per_1m
        )
        # Fallback si la API no mandó desglose: tratar totales como text (más barato)
        # para no inflar; mejor subestimar que inventar audio.
        fallback_in = 0.0
        fallback_out = 0.0
        detailed = (
            self.audio_input_tokens
            + self.audio_output_tokens
            + self.text_input_tokens
            + self.text_output_tokens
            + self.audio_cached_input_tokens
            + self.text_cached_input_tokens
        )
        if detailed == 0 and (self.total_prompt_tokens or self.total_completion_tokens):
            fallback_in = (self.total_prompt_tokens / per_m) * float(
                s.call_metrics_price_rt_text_in_per_1m
            )
            fallback_out = (self.total_completion_tokens / per_m) * float(
                s.call_metrics_price_rt_text_out_per_1m
            )
        transcribe = (self.stt_audio_seconds / 60.0) * float(
            s.call_metrics_price_rt_transcribe_per_minute
        )
        total = (
            audio_in
            + audio_out
            + text_in
            + text_out
            + audio_cached
            + text_cached
            + fallback_in
            + fallback_out
            + transcribe
        )
        self.last_cost_breakdown = {
            "mode": "realtime",
            "audio_input": round(audio_in, 6),
            "audio_output": round(audio_out, 6),
            "text_input": round(text_in, 6),
            "text_output": round(text_out, 6),
            "audio_cached_input": round(audio_cached, 6),
            "text_cached_input": round(text_cached, 6),
            "fallback_text_input": round(fallback_in, 6),
            "fallback_text_output": round(fallback_out, 6),
            "transcribe": round(transcribe, 6),
            "tokens": {
                "audio_in": self.audio_input_tokens,
                "audio_out": self.audio_output_tokens,
                "text_in": self.text_input_tokens,
                "text_out": self.text_output_tokens,
                "audio_cached_in": self.audio_cached_input_tokens,
                "text_cached_in": self.text_cached_input_tokens,
                "prompt_total": self.total_prompt_tokens,
                "completion_total": self.total_completion_tokens,
            },
            "total": round(total, 6),
        }
        return Decimal(str(round(total, 6)))

    def _close_idle(self) -> None:
        if self._idle_since_mono is None:
            return
        self.silence_seconds += max(0.0, time.monotonic() - self._idle_since_mono)
        self._idle_since_mono = None

    def build_row(self, *, disconnect_reason: str | None = None) -> CallSessions:
        self._close_idle()
        if self._speech_started_mono is not None:
            self.stt_audio_seconds += max(
                0.0, time.monotonic() - self._speech_started_mono
            )
            self._speech_started_mono = None
        end = _utc_now()
        self.end_time = end
        if disconnect_reason:
            self.disconnect_reason = disconnect_reason
        duration = max(0.0, (end - self.start_time).total_seconds())
        silence_pct = None
        if duration > 0:
            silence_pct = round(min(100.0, (self.silence_seconds / duration) * 100.0), 2)
        ttfa = self.time_to_first_audio_ms
        if ttfa is None and self.first_bot_audio_time is not None:
            ttfa = int(
                (self.first_bot_audio_time - self.start_time).total_seconds() * 1000
            )
        estimated = self.estimate_cost()
        providers = dict(self.providers)
        providers.setdefault("mode", self.mode)
        providers["cost_breakdown"] = dict(self.last_cost_breakdown)
        return CallSessions(
            conversation_id=self.conversation_id,
            start_time=self.start_time,
            answer_time=self.answer_time,
            first_bot_audio_time=self.first_bot_audio_time,
            end_time=end,
            duration_seconds=round(duration, 3),
            time_to_first_audio_ms=ttfa,
            user_turns=self.user_turns,
            bot_turns=self.bot_turns,
            stt_requests=self.stt_requests,
            llm_requests=self.llm_requests,
            tts_requests=self.tts_requests,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
            stt_audio_seconds=round(self.stt_audio_seconds, 3),
            tts_characters=self.tts_characters,
            estimated_cost=estimated,
            avg_llm_latency_ms=_avg(self._llm_latencies),
            avg_tts_latency_ms=_avg(self._tts_latencies),
            avg_stt_latency_ms=_avg(self._stt_latencies),
            avg_response_latency_ms=_avg(self._response_latencies),
            interruptions=self.interruptions,
            silence_seconds=round(self.silence_seconds, 3),
            silence_percent=silence_pct,
            disconnect_reason=(self.disconnect_reason or "unknown")[:255],
            turn_timings_json={"turns": self.turn_timings[-50:]},
            providers_json=providers,
            created_at=_utc_now(),
        )

    def flush(
        self,
        db: Session,
        *,
        disconnect_reason: str | None = None,
    ) -> Optional[CallSessions]:
        if not self.enabled:
            return None
        if self._flushed:
            return None
        self._flushed = True
        try:
            row = self.build_row(disconnect_reason=disconnect_reason)
            saved = CallSessionsRepository(db).create(row)
            logger.info(
                "call_sessions id=%s conv=%s duration=%.1fs cost=%s reason=%s "
                "turns=%s/%s",
                saved.id,
                self.conversation_id,
                saved.duration_seconds or 0,
                saved.estimated_cost,
                saved.disconnect_reason,
                saved.user_turns,
                saved.bot_turns,
            )
            return saved
        except Exception:
            logger.exception(
                "No se pudo persistir call_sessions conv=%s", self.conversation_id
            )
            self._flushed = False
            return None
