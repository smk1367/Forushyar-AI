import base64
import os
import httpx

from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.conversation import Conversation
from app.schemas import AssistantChatRequest
from app.api.assistant import assistant_chat

router = APIRouter(tags=["AI voice assistant"])

SPEECH_BASE_URL = os.getenv("SPEECH_BASE_URL", "http://speech:8001").rstrip("/")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/assistant/voice")
async def assistant_voice(
    audio: UploadFile = File(...),
    language: str = Form(default="fa"),
    conversation_id: UUID | None = Form(default=None),
    customer_id: UUID | None = Form(default=None),
    session: AsyncSession = Depends(get_db),
):
    if language not in {"fa", "auto", "en"}:
        raise HTTPException(status_code=422, detail="زبان پشتیبانی نمی‌شود")

    audio_data = await audio.read(MAX_UPLOAD_BYTES + 1)
    if not audio_data:
        raise HTTPException(status_code=422, detail="فایل صوتی خالی است")
    if len(audio_data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="حجم فایل صوتی بیش از 25 مگابایت است")

    filename = audio.filename or "audio.wav"
    content_type = audio.content_type or "application/octet-stream"

    timeout = httpx.Timeout(300.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 1. Speech-to-Text: Whisper
            stt_response = await client.post(
                f"{SPEECH_BASE_URL}/v1/audio/transcriptions",
                files={"audio": (filename, audio_data, content_type)},
                data={"language": language},
            )
            stt_response.raise_for_status()
            transcript_data = stt_response.json()

            transcript = str(transcript_data.get("text", "")).strip()
            if not transcript:
                raise HTTPException(
                    status_code=422,
                    detail="متنی از فایل صوتی تشخیص داده نشد",
                )

            # 2. Reuse the existing text assistant and its catalog grounding.
            chat_payload = AssistantChatRequest(
                message=transcript,
                conversation_id=conversation_id,
                customer_id=customer_id,
            )
            chat_result = await assistant_chat(chat_payload, session)

            # Mark conversations created/used through this endpoint as VOICE.
            conversation = await session.get(Conversation, chat_result.conversation_id)
            if conversation is not None and conversation.channel != "VOICE":
                conversation.channel = "VOICE"
                await session.commit()

            # 3. Text-to-Speech: Piper
            tts_response = await client.post(
                f"{SPEECH_BASE_URL}/v1/audio/speech",
                json={"text": chat_result.reply},
            )
            tts_response.raise_for_status()
            audio_base64 = base64.b64encode(tts_response.content).decode("ascii")

    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="زمان پاسخ سرویس صوتی یا هوش مصنوعی به پایان رسید",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail="یکی از سرویس‌های تبدیل صدا پاسخ نامعتبر داد",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="ارتباط با سرویس صوتی برقرار نشد",
        ) from exc

    return {
        "transcript": transcript,
        "reply": chat_result.reply,
        "conversation_id": str(chat_result.conversation_id),
        "user_message_id": str(chat_result.user_message_id),
        "assistant_message_id": str(chat_result.assistant_message_id),
        "model": chat_result.model,
        "grounded_product_skus": chat_result.grounded_product_skus,
        "audio_format": "wav",
        "audio_base64": audio_base64,
    }
