from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.employee import Employee
from app.security import require_csrf
from app.models.product import Product
from app.models.sales_order import SalesOrder
from app.models.warehouse import Warehouse
from app.services.sales_orders import (
    cancel_sales_order,
    create_sales_order,
    get_sales_order,
)
from app.schemas import (
    CustomerCreate,
    CustomerRead,
    SalesOrderCancelRequest,
    SalesOrderCreate,
    SalesOrderRead,
)

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



@router.get("/customers", response_model=list[CustomerRead])
async def list_dashboard_customers(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """List customers for authenticated CRM-capable dashboard employees."""
    if employee.role not in {"ADMIN", "MANAGER", "SALES", "SUPPORT"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده مشتریان کافی نیست")
    stmt = select(Customer).order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            Customer.full_name.ilike(pattern)
            | Customer.phone.ilike(pattern)
            | Customer.email.ilike(pattern)
        )
    return list((await session.scalars(stmt)).all())


@router.post(
    "/customers",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_customer(
    payload: CustomerCreate,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """Create a customer from the session-authenticated dashboard."""
    if employee.role not in {"ADMIN", "MANAGER", "SALES"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای ثبت مشتری کافی نیست")

    customer = Customer(**payload.model_dump())
    session.add(customer)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="شماره تلفن قبلاً برای مشتری دیگری ثبت شده است",
        ) from exc
    await session.refresh(customer)
    return customer


def _require_order_reader(employee: Employee) -> None:
    if employee.role not in {"ADMIN", "MANAGER", "SALES", "SUPPORT"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده سفارش‌ها کافی نیست")


def _require_order_writer(employee: Employee) -> None:
    if employee.role not in {"ADMIN", "MANAGER", "SALES"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای عملیات سفارش کافی نیست")


@router.get("/order-options")
async def dashboard_order_options(
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """Return active customers, products and warehouses for the order form."""
    _require_order_writer(employee)
    customers = list((await session.scalars(
        select(Customer).order_by(Customer.full_name).limit(200)
    )).all())
    products = list((await session.scalars(
        select(Product).where(Product.is_active.is_(True)).order_by(Product.sku).limit(500)
    )).all())
    warehouses = list((await session.scalars(
        select(Warehouse).where(Warehouse.is_active.is_(True)).order_by(Warehouse.code).limit(100)
    )).all())
    return {
        "customers": [
            {"id": str(item.id), "full_name": item.full_name, "phone": item.phone}
            for item in customers
        ],
        "products": [
            {"id": str(item.id), "sku": item.sku, "name": item.name, "unit": item.unit}
            for item in products
        ],
        "warehouses": [
            {"id": str(item.id), "code": item.code, "name": item.name}
            for item in warehouses
        ],
    }


@router.get("/orders")
async def list_dashboard_orders(
    q: str | None = Query(default=None, max_length=120),
    order_status: str | None = Query(default=None, alias="status", pattern="^(PENDING_PAYMENT|CANCELLED)$"),
    customer_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """List recent orders for authenticated CRM-capable dashboard employees."""
    _require_order_reader(employee)
    stmt = (
        select(
            SalesOrder.id,
            SalesOrder.customer_id,
            Customer.full_name,
            SalesOrder.status,
            SalesOrder.currency_code,
            SalesOrder.subtotal,
            SalesOrder.created_at,
        )
        .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
        .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if order_status:
        stmt = stmt.where(SalesOrder.status == order_status)
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            Customer.full_name.ilike(pattern)
            | SalesOrder.id.cast(str).ilike(pattern)
        )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": str(row.id),
            "customer_id": str(row.customer_id) if row.customer_id else None,
            "customer_name": row.full_name,
            "status": row.status,
            "currency_code": row.currency_code,
            "subtotal": str(row.subtotal),
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/orders/{order_id}", response_model=SalesOrderRead)
async def read_dashboard_order(
    order_id: UUID,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """Read order details, line items, reservations and order events."""
    _require_order_reader(employee)
    return await get_sales_order(session, order_id)


@router.post(
    "/orders",
    response_model=SalesOrderRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_order(
    payload: SalesOrderCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """Create an order using current prices and atomically reserve actual inventory."""
    _require_order_writer(employee)
    return await create_sales_order(session, payload, idempotency_key)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=SalesOrderRead,
    dependencies=[Depends(require_csrf)],
)
async def cancel_dashboard_order(
    order_id: UUID,
    payload: SalesOrderCancelRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=120),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    """Cancel only eligible pending-payment orders and release reservations atomically."""
    _require_order_writer(employee)
    return await cancel_sales_order(session, order_id, idempotency_key, payload.reason)
