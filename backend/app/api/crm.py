from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage
from app.models.customer import Customer
from app.schemas import (
    ConversationAction,
    ConversationCreate,
    ConversationMessageRead,
    ConversationRead,
    CustomerCreate,
    CustomerRead,
)

router = APIRouter(tags=["CRM"], dependencies=[Depends(require_api_key)])


@router.post("/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer(payload: CustomerCreate, session: AsyncSession = Depends(get_db)):
    customer = Customer(**payload.model_dump())
    session.add(customer)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="شماره تلفن قبلاً برای مشتری دیگری ثبت شده است") from exc
    await session.refresh(customer)
    return customer


@router.get("/customers", response_model=list[CustomerRead])
async def list_customers(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Customer).order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    if q:
        stmt = stmt.where(
            Customer.full_name.ilike(f"%{q}%")
            | Customer.phone.ilike(f"%{q}%")
            | Customer.email.ilike(f"%{q}%")
        )
    return list((await session.scalars(stmt)).all())


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(payload: ConversationCreate, session: AsyncSession = Depends(get_db)):
    if payload.customer_id:
        customer = await session.get(Customer, payload.customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="مشتری یافت نشد")
    conversation = Conversation(**payload.model_dump())
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    customer_id: UUID | None = None,
    conversation_status: str | None = Query(default=None, alias="status", pattern="^(OPEN|HANDOFF_REQUIRED|CLOSED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
    if customer_id:
        stmt = stmt.where(Conversation.customer_id == customer_id)
    if conversation_status:
        stmt = stmt.where(Conversation.status == conversation_status)
    return list((await session.scalars(stmt)).all())


@router.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessageRead])
async def list_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    exists = await session.get(Conversation, conversation_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="گفتگو یافت نشد")
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.scalars(stmt)).all())


@router.post("/conversations/{conversation_id}/handoff", response_model=ConversationRead)
async def handoff_conversation(
    conversation_id: UUID,
    payload: ConversationAction,
    session: AsyncSession = Depends(get_db),
):
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="گفتگو یافت نشد")
    if conversation.status == "CLOSED":
        raise HTTPException(status_code=409, detail="گفتگوی بسته‌شده قابل ارجاع نیست")
    conversation.status = "HANDOFF_REQUIRED"
    conversation.subject = conversation.subject or payload.reason[:240]
    await session.commit()
    await session.refresh(conversation)
    return conversation
