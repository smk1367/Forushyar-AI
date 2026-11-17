from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.employee import Employee
from app.models.sales_order import SalesOrder, SalesOrderLine

router = APIRouter(
    prefix="/api/v1/dashboard/sales-reports",
    tags=["Dashboard sales reports"],
    dependencies=[Depends(current_employee)],
)

REPORT_ROLES = {"ADMIN", "MANAGER", "SALES"}


def _require_report_role(employee: Employee) -> None:
    if employee.role not in REPORT_ROLES:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده گزارش‌های فروش کافی نیست")


def _date_window(from_date: date | None, to_date: date | None) -> tuple[datetime, datetime, date, date]:
    end_day = to_date or datetime.now(timezone.utc).date()
    start_day = from_date or (end_day - timedelta(days=29))
    if start_day > end_day:
        raise HTTPException(status_code=422, detail="تاریخ شروع نباید بعد از تاریخ پایان باشد")
    if (end_day - start_day).days > 366:
        raise HTTPException(status_code=422, detail="بازه گزارش نباید بیشتر از ۳۶۷ روز باشد")
    return (
        datetime.combine(start_day, time.min, tzinfo=timezone.utc),
        datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc),
        start_day,
        end_day,
    )


@router.get("/summary")
async def get_dashboard_sales_report_summary(
    from_date: date | None = None,
    to_date: date | None = None,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_report_role(employee)
    start_at, end_at, start_day, end_day = _date_window(from_date, to_date)
    filters = (SalesOrder.created_at >= start_at, SalesOrder.created_at < end_at)

    status_stmt = (
        select(
            SalesOrder.status,
            SalesOrder.currency_code,
            func.count(SalesOrder.id),
            func.coalesce(func.sum(SalesOrder.subtotal), 0),
        )
        .where(*filters)
        .group_by(SalesOrder.status, SalesOrder.currency_code)
        .order_by(SalesOrder.status, SalesOrder.currency_code)
    )
    status_rows = (await session.execute(status_stmt)).all()
    total_orders = sum(int(row[2]) for row in status_rows)
    pending_count = sum(int(row[2]) for row in status_rows if row[0] == "PENDING_PAYMENT")
    cancelled_count = sum(int(row[2]) for row in status_rows if row[0] == "CANCELLED")
    pending_amounts = [
        {"currency_code": currency, "amount": str(amount), "order_count": int(count)}
        for state, currency, count, amount in status_rows if state == "PENDING_PAYMENT"
    ]
    cancelled_amounts = [
        {"currency_code": currency, "amount": str(amount), "order_count": int(count)}
        for state, currency, count, amount in status_rows if state == "CANCELLED"
    ]

    daily_stmt = (
        select(
            func.date(SalesOrder.created_at).label("order_day"),
            SalesOrder.status,
            SalesOrder.currency_code,
            func.count(SalesOrder.id),
            func.coalesce(func.sum(SalesOrder.subtotal), 0),
        )
        .where(*filters)
        .group_by(func.date(SalesOrder.created_at), SalesOrder.status, SalesOrder.currency_code)
        .order_by(func.date(SalesOrder.created_at), SalesOrder.status, SalesOrder.currency_code)
    )
    daily_rows = (await session.execute(daily_stmt)).all()
    daily = [
        {"date": row[0].isoformat(), "status": row[1], "currency_code": row[2], "order_count": int(row[3]), "order_amount": str(row[4])}
        for row in daily_rows
    ]

    top_stmt = (
        select(
            SalesOrderLine.sku_snapshot,
            SalesOrderLine.product_name_snapshot,
            SalesOrderLine.currency_code,
            func.coalesce(func.sum(SalesOrderLine.quantity), 0),
            func.coalesce(func.sum(SalesOrderLine.line_total), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id)
        .where(*filters, SalesOrder.status != "CANCELLED")
        .group_by(SalesOrderLine.sku_snapshot, SalesOrderLine.product_name_snapshot, SalesOrderLine.currency_code)
        .order_by(func.sum(SalesOrderLine.line_total).desc())
        .limit(10)
    )
    top_rows = (await session.execute(top_stmt)).all()
    top_products = [
        {"sku": row[0], "product_name": row[1], "currency_code": row[2], "quantity": str(row[3]), "order_amount": str(row[4])}
        for row in top_rows
    ]

    return {
        "from_date": start_day.isoformat(),
        "to_date": end_day.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_orders": total_orders,
        "pending_payment_orders": pending_count,
        "cancelled_orders": cancelled_count,
        "pending_order_amounts_by_currency": pending_amounts,
        "cancelled_order_amounts_by_currency": cancelled_amounts,
        "orders_by_day": daily,
        "top_products": top_products,
        "payments_available": False,
        "revenue_note": "در نسخه فعلی، گردش پرداخت/وصول وجه پیاده‌سازی نشده است؛ مبالغ این گزارش ارزش سفارش‌ها هستند و درآمد وصول‌شده محسوب نمی‌شوند.",
    }
