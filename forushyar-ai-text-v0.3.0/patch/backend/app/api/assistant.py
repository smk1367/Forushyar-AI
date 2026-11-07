from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.core.config import settings
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage
from app.models.customer import Customer
from app.schemas import AssistantChatRequest, AssistantChatResponse, AssistantStatusRead
from app.services.assistant import build_catalog_context, build_llm_messages, request_llm

router = APIRouter(prefix="/assistant", tags=["AI assistant"], dependencies=[Depends(require_api_key)])


@router.get("/status", response_model=AssistantStatusRead)
async def assistant_status():
    return AssistantStatusRead(
        configured=settings.llm_is_configured,
        provider_type="openai-compatible",
        model=settings.llm_model if settings.llm_is_configured else None,
        mode="read-only-product-and-inventory-advice",
    )


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(payload: AssistantChatRequest, session: AsyncSession = Depends(get_db)):
    if not settings.llm_is_configured:
        raise HTTPException(
            status_code=503,
            detail="موتور هوش مصنوعی هنوز پیکربندی نشده است؛ ابتدا LLM_BASE_URL و LLM_MODEL را تنظیم کنید.",
        )

    conversation: Conversation | None
    if payload.conversation_id:
        conversation = await session.get(Conversation, payload.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="گفتگو یافت نشد")
        if conversation.status == "CLOSED":
            raise HTTPException(status_code=409, detail="این گفتگو بسته شده است")
        if payload.customer_id and conversation.customer_id != payload.customer_id:
            raise HTTPException(status_code=409, detail="مشتری با گفتگوی انتخاب‌شده مطابقت ندارد")
    else:
        if payload.customer_id and await session.get(Customer, payload.customer_id) is None:
            raise HTTPException(status_code=404, detail="مشتری یافت نشد")
        conversation = Conversation(customer_id=payload.customer_id, channel="TEXT", status="OPEN")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

    conversation_id = conversation.id

    history_rows = list((await session.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(settings.assistant_history_limit)
    )).all())
    history_rows.reverse()
    history = [{"role": row.role, "content": row.content} for row in history_rows if row.role in {"user", "assistant"}]

    _, catalog_json = await build_catalog_context(session, payload.message)
    await session.rollback()  # release the DB transaction before making the external LLM request

    messages = build_llm_messages(history, payload.message, catalog_json)
    try:
        reply = await request_llm(messages)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="پاسخ‌گویی هوش مصنوعی بیش از زمان مجاز طول کشید") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"سرویس مدل زبانی خطای HTTP {exc.response.status_code} برگرداند") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="ارتباط با سرویس مدل زبانی ناموفق بود") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="موتور هوش مصنوعی پیکربندی نشده است") from exc

    now = datetime.now(timezone.utc)
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="گفتگو دیگر در دسترس نیست")
    if conversation.status == "CLOSED":
        raise HTTPException(status_code=409, detail="این گفتگو در زمان پردازش بسته شده است")

    user_record = ConversationMessage(
        conversation_id=conversation_id, role="user", content=payload.message
    )
    assistant_record = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=reply,
        provider="openai-compatible",
        model=settings.llm_model,
    )
    session.add_all([user_record, assistant_record])
    conversation.last_message_at = now
    conversation.updated_at = now
    await session.commit()
    await session.refresh(user_record)
    await session.refresh(assistant_record)

    # Return only product identifiers that were actually present in the grounded context.
    # The context JSON can be empty or the explicit no-match sentence.
    import json
    try:
        context_items = json.loads(catalog_json)
        grounded_skus = [item["sku"] for item in context_items if isinstance(item, dict) and "sku" in item]
    except (ValueError, TypeError):
        grounded_skus = []

    return AssistantChatResponse(
        conversation_id=conversation_id,
        user_message_id=user_record.id,
        assistant_message_id=assistant_record.id,
        reply=reply,
        model=settings.llm_model or "",
        grounded_product_skus=grounded_skus,
    )
