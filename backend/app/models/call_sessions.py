from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CallSessions(Base):
    __tablename__ = "call_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id"), nullable=False, index=True
    )
    start_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    answer_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_bot_audio_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    time_to_first_audio_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bot_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stt_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tts_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    stt_audio_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tts_characters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    avg_llm_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_tts_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_stt_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_response_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    interruptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    silence_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    silence_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disconnect_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    turn_timings_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    providers_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation = relationship("Conversation", back_populates="call_sessions")
