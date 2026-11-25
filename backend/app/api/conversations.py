import base64
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.conversation import (
    AudioBase64In,
    AudioConversationResponse,
    ConversationCreate,
    ConversationOut,
    TextConversationIn,
)
from app.services.auth_service import get_current_user
from app.services.conversation_service import ConversationService
from app.services.tts_stream import get_tts_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationOut)
async def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConversationOut:
    return await ConversationService(db).create(user, payload)


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ConversationOut:
    return ConversationService(db).get(conversation_id)


@router.post("/{conversation_id}/audio", response_model=AudioConversationResponse)
async def process_conversation_audio(
    conversation_id: str,
    audio: UploadFile = File(...),
    proyect_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AudioConversationResponse:
    content = await audio.read()
    logger.info(
        "audio multipart conversation=%s bytes=%s content_type=%s filename=%s",
        conversation_id,
        len(content),
        audio.content_type,
        audio.filename,
    )
    return await ConversationService(db).process_audio(
        conversation_id, content, proyect_id=proyect_id
    )


@router.post("/{conversation_id}/audio-base64", response_model=AudioConversationResponse)
async def process_conversation_audio_base64(
    conversation_id: str,
    payload: AudioBase64In,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AudioConversationResponse:
    """JSON base64 upload — más fiable desde React Native/Expo que multipart."""
    raw = payload.audio_base64.strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        content = base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid audio_base64: {exc}",
        ) from exc

    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Empty audio payload",
        )

    logger.info(
        "audio base64 conversation=%s bytes=%s mime=%s filename=%s",
        conversation_id,
        len(content),
        payload.mime_type,
        payload.filename,
    )
    return await ConversationService(db).process_audio(
        conversation_id, content, proyect_id=payload.proyect_id
    )


@router.post("/{conversation_id}/text", response_model=AudioConversationResponse)
async def process_conversation_text(
    conversation_id: str,
    payload: TextConversationIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AudioConversationResponse:
    return await ConversationService(db).process_text(
        conversation_id, payload.text, proyect_id=payload.proyect_id
    )


@router.get("/{conversation_id}/tts/{token}")
async def stream_conversation_tts(
    conversation_id: str,
    token: str,
    _: User = Depends(get_current_user),
) -> StreamingResponse:
    """MP3/WAV que empieza a salir en el primer byte de Aura."""
    file_token = token.rsplit(".", 1)[0]
    stream = get_tts_stream(file_token)
    if stream is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TTS stream expired")
    media = "audio/wav" if token.endswith(".wav") else "audio/mpeg"
    return StreamingResponse(
        stream.iter_chunks(),
        media_type=media,
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Conversation-Id": conversation_id,
        },
    )
