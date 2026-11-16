from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.employee import Employee
from app.models.product import Product
from app.models.sales_order import SalesOrder

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"], dependencies=[Depends(current_employee)])


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    week_start = today_start - timedelta(days=6)

    customers = await session.scalar(select(func.count()).select_from(Customer)) or 0
    products = await session.scalar(select(func.count()).select_from(Product).where(Product.is_active.is_(True))) or 0
    employees = await session.scalar(select(func.count()).select_from(Employee).where(Employee.is_active.is_(True))) or 0
    open_conversations = await session.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.status.in_(["OPEN", "HANDOFF_REQUIRED"]))
    ) or 0
    today_orders = await session.scalar(
        select(func.count()).select_from(SalesOrder).where(SalesOrder.created_at >= today_start)
    ) or 0
    week_order_rows = (await session.execute(
        select(func.date(SalesOrder.created_at), func.count(), func.coalesce(func.sum(SalesOrder.subtotal), 0))
        .where(SalesOrder.created_at >= week_start)
        .group_by(func.date(SalesOrder.created_at))
        .order_by(func.date(SalesOrder.created_at))
    )).all()
    recent_orders = (await session.execute(
        select(SalesOrder.id, SalesOrder.status, SalesOrder.subtotal, SalesOrder.currency_code, SalesOrder.created_at, Customer.full_name)
        .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
        .order_by(SalesOrder.created_at.desc())
        .limit(8)
    )).all()

    return {
        "generated_at": now,
        "kpis": {
            "customers": customers,
            "active_products": products,
            "active_employees": employees,
            "open_conversations": open_conversations,
            "orders_today": today_orders,
        },
        "orders_by_day": [
            {"date": str(day), "count": count, "subtotal": str(subtotal)}
            for day, count, subtotal in week_order_rows
        ],
        "recent_orders": [
            {"id": str(row.id), "status": row.status, "subtotal": str(row.subtotal),
             "currency_code": row.currency_code, "created_at": row.created_at,
             "customer_name": row.full_name}
            for row in recent_orders
        ],
    }
