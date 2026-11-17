from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage
from app.models.customer import Customer
from app.models.employee import Employee
from app.security import require_csrf

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Dashboard conversations"],
    dependencies=[Depends(current_employee)],
)

CONVERSATION_READERS = {"ADMIN", "MANAGER", "SUPPORT", "SALES"}
CONVERSATION_MANAGERS = {"ADMIN", "MANAGER"}


class AssignConversationRequest(BaseModel):
    employee_id: UUID
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"


class ConversationStatusRequest(BaseModel):
    status: Literal["OPEN", "HANDOFF_REQUIRED", "CLOSED"]
    reason: str = Field(min_length=3, max_length=240)


class ConversationReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _require_reader(employee: Employee) -> None:
    if employee.role not in CONVERSATION_READERS:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده مکالمات کافی نیست")


def _require_conversation_access(employee: Employee, conversation: Conversation) -> None:
    _require_reader(employee)
    if employee.role == "SALES" and conversation.assigned_employee_id != employee.id:
        raise HTTPException(status_code=403, detail="این مکالمه به حساب کاربری شما تخصیص داده نشده است")


@router.get("/conversation-agents")
async def list_conversation_agents(
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    if employee.role not in CONVERSATION_MANAGERS | {"SUPPORT"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده اپراتورها کافی نیست")
    stmt = select(Employee).where(Employee.is_active.is_(True), Employee.role.in_(["SUPPORT", "SALES"])).order_by(Employee.full_name)
    return [
        {"id": str(item.id), "full_name": item.full_name, "email": item.email, "role": item.role}
        for item in (await session.scalars(stmt)).all()
    ]


@router.get("/conversations")
async def list_dashboard_conversations(
    q: str | None = Query(default=None, max_length=120),
    conversation_status: str | None = Query(default=None, alias="status", pattern="^(OPEN|HANDOFF_REQUIRED|CLOSED)$"),
    channel: str | None = Query(default=None, pattern="^(TEXT|WEB|WHATSAPP|PHONE)$"),
    priority: str | None = Query(default=None, pattern="^(LOW|NORMAL|HIGH|URGENT)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_reader(employee)
    stmt = (
        select(Conversation, Customer, Employee)
        .outerjoin(Customer, Customer.id == Conversation.customer_id)
        .outerjoin(Employee, Employee.id == Conversation.assigned_employee_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit).offset(offset)
    )
    if employee.role == "SALES":
        stmt = stmt.where(Conversation.assigned_employee_id == employee.id)
    if conversation_status:
        stmt = stmt.where(Conversation.status == conversation_status)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if priority:
        stmt = stmt.where(Conversation.priority == priority)
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(or_(
            Conversation.subject.ilike(pattern),
            Customer.full_name.ilike(pattern),
            Customer.phone.ilike(pattern),
        ))
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": str(conversation.id),
            "customer_id": str(conversation.customer_id) if conversation.customer_id else None,
            "customer_name": customer.full_name if customer else "مشتری ناشناس",
            "customer_phone": customer.phone if customer else None,
            "channel": conversation.channel,
            "status": conversation.status,
            "priority": conversation.priority,
            "subject": conversation.subject,
            "assigned_employee_id": str(conversation.assigned_employee_id) if conversation.assigned_employee_id else None,
            "assigned_employee_name": assigned.full_name if assigned else None,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "last_message_at": conversation.last_message_at,
        }
        for conversation, customer, assigned in rows
    ]


@router.get("/conversations/{conversation_id}")
async def read_dashboard_conversation(
    conversation_id: UUID,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="مکالمه یافت نشد")
    _require_conversation_access(employee, conversation)
    customer = await session.get(Customer, conversation.customer_id) if conversation.customer_id else None
    assigned = await session.get(Employee, conversation.assigned_employee_id) if conversation.assigned_employee_id else None
    return {
        "id": str(conversation.id),
        "customer_id": str(conversation.customer_id) if conversation.customer_id else None,
        "customer_name": customer.full_name if customer else "مشتری ناشناس",
        "customer_phone": customer.phone if customer else None,
        "channel": conversation.channel,
        "status": conversation.status,
        "priority": conversation.priority,
        "subject": conversation.subject,
        "assigned_employee_id": str(conversation.assigned_employee_id) if conversation.assigned_employee_id else None,
        "assigned_employee_name": assigned.full_name if assigned else None,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "last_message_at": conversation.last_message_at,
    }


@router.get("/conversations/{conversation_id}/messages")
async def list_dashboard_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="مکالمه یافت نشد")
    _require_conversation_access(employee, conversation)
    stmt = select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id
    ).order_by(ConversationMessage.created_at, ConversationMessage.id).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


@router.post(
    "/conversations/{conversation_id}/assign",
    dependencies=[Depends(require_csrf)],
)
async def assign_dashboard_conversation(
    conversation_id: UUID,
    payload: AssignConversationRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    if employee.role not in CONVERSATION_MANAGERS | {"SUPPORT"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای تخصیص مکالمه کافی نیست")
    conversation = await session.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    if conversation is None:
        raise HTTPException(status_code=404, detail="مکالمه یافت نشد")
    if conversation.status == "CLOSED":
        raise HTTPException(status_code=409, detail="مکالمه بسته‌شده قابل تخصیص نیست")
    if employee.role == "SUPPORT" and payload.employee_id != employee.id:
        raise HTTPException(status_code=403, detail="اپراتور پشتیبانی فقط می‌تواند مکالمه را به خودش تخصیص دهد")
    target = await session.get(Employee, payload.employee_id)
    if target is None or not target.is_active or target.role not in {"SUPPORT", "SALES"}:
        raise HTTPException(status_code=422, detail="اپراتور انتخاب‌شده فعال یا واجد نقش پشتیبانی/فروش نیست")
    conversation.assigned_employee_id = target.id
    conversation.assigned_at = datetime.now(timezone.utc)
    conversation.priority = payload.priority
    if conversation.status == "HANDOFF_REQUIRED":
        conversation.status = "OPEN"
    conversation.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": str(conversation.id), "status": conversation.status, "priority": conversation.priority, "assigned_employee_id": str(target.id), "assigned_employee_name": target.full_name}


@router.post(
    "/conversations/{conversation_id}/reply",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def reply_dashboard_conversation(
    conversation_id: UUID,
    payload: ConversationReplyRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    if employee.role not in CONVERSATION_MANAGERS | {"SUPPORT", "SALES"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای پاسخ‌گویی کافی نیست")
    conversation = await session.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    if conversation is None:
        raise HTTPException(status_code=404, detail="مکالمه یافت نشد")
    _require_conversation_access(employee, conversation)
    if conversation.status == "CLOSED":
        raise HTTPException(status_code=409, detail="مکالمه بسته شده است")
    if conversation.assigned_employee_id is None:
        conversation.assigned_employee_id = employee.id
        conversation.assigned_at = datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=payload.message.strip(),
        provider="dashboard-agent",
    )
    session.add(message)
    conversation.status = "OPEN"
    conversation.last_message_at = now
    conversation.updated_at = now
    await session.commit()
    await session.refresh(message)
    return message


@router.post(
    "/conversations/{conversation_id}/status",
    dependencies=[Depends(require_csrf)],
)
async def update_dashboard_conversation_status(
    conversation_id: UUID,
    payload: ConversationStatusRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    if employee.role not in CONVERSATION_MANAGERS | {"SUPPORT", "SALES"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای تغییر وضعیت مکالمه کافی نیست")
    conversation = await session.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    if conversation is None:
        raise HTTPException(status_code=404, detail="مکالمه یافت نشد")
    _require_conversation_access(employee, conversation)
    if employee.role == "SALES" and conversation.assigned_employee_id != employee.id:
        raise HTTPException(status_code=403, detail="این مکالمه به حساب کاربری شما تخصیص داده نشده است")
    if conversation.status == "CLOSED" and payload.status != "OPEN" and payload.status != "CLOSED":
        raise HTTPException(status_code=409, detail="مکالمه بسته‌شده ابتدا باید بازگشایی شود")
    conversation.status = payload.status
    conversation.updated_at = datetime.now(timezone.utc)
    if payload.status == "HANDOFF_REQUIRED":
        conversation.subject = conversation.subject or payload.reason
    await session.commit()
    return {"id": str(conversation.id), "status": conversation.status, "updated_at": conversation.updated_at}
