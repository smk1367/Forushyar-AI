from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.sales import SalesOrder, SalesPayment, SalesQuote
from app.sales_schemas import (
    SalesOrderAction, SalesOrderCreate, SalesOrderRead, SalesPaymentConfirmation,
    SalesPaymentCreate, SalesPaymentRead, SalesQuoteCreate, SalesQuoteRead,
)
from app.services.sales import (
    cancel_order, confirm_payment, convert_quote_to_order, create_order,
    create_payment, create_quote, deliver_order, list_orders, list_quotes,
    send_quote, ship_order,
)

router = APIRouter(
    prefix="/api/v1/sales",
    tags=["Sales"],
    dependencies=[Depends(require_api_key)],
)
web_router = APIRouter(tags=["Sales UI"])
WEB_FILE = Path(__file__).resolve().parents[1] / "web" / "sales.html"


@web_router.get("/admin/sales", response_class=HTMLResponse, include_in_schema=False)
async def sales_page():
    return HTMLResponse(
        content=WEB_FILE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/quotes", response_model=SalesQuoteRead, status_code=status.HTTP_201_CREATED)
async def create_sales_quote(payload: SalesQuoteCreate, session: AsyncSession = Depends(get_db)):
    return await create_quote(session, payload)


@router.get("/quotes", response_model=list[SalesQuoteRead])
async def get_sales_quotes(
    quote_status: str | None = Query(default=None, alias="status", pattern="^(DRAFT|SENT|CONVERTED|REJECTED|EXPIRED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    return await list_quotes(session, quote_status=quote_status, limit=limit, offset=offset)


@router.post("/quotes/{quote_id}/send", response_model=SalesQuoteRead)
async def send_sales_quote(quote_id: UUID, session: AsyncSession = Depends(get_db)):
    return await send_quote(session, quote_id)


@router.post("/quotes/{quote_id}/convert", response_model=SalesOrderRead, status_code=status.HTTP_201_CREATED)
async def convert_sales_quote(quote_id: UUID, session: AsyncSession = Depends(get_db)):
    return await convert_quote_to_order(session, quote_id)


@router.post("/orders", response_model=SalesOrderRead, status_code=status.HTTP_201_CREATED)
async def create_sales_order(payload: SalesOrderCreate, session: AsyncSession = Depends(get_db)):
    return await create_order(session, payload)


@router.get("/orders", response_model=list[SalesOrderRead])
async def get_sales_orders(
    order_status: str | None = Query(default=None, alias="status", pattern="^(AWAITING_PAYMENT|CONFIRMED|SHIPPED|DELIVERED|CANCELLED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    return await list_orders(session, order_status=order_status, limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=SalesOrderRead)
async def get_sales_order(order_id: UUID, session: AsyncSession = Depends(get_db)):
    from app.services.sales import _load_order
    return await _load_order(session, order_id)


@router.post("/orders/{order_id}/payments", response_model=SalesPaymentRead, status_code=status.HTTP_201_CREATED)
async def create_sales_payment(
    order_id: UUID, payload: SalesPaymentCreate, session: AsyncSession = Depends(get_db)
):
    return await create_payment(session, order_id, payload)


@router.post("/payments/{payment_id}/confirm", response_model=SalesPaymentRead)
async def confirm_sales_payment(
    payment_id: UUID, payload: SalesPaymentConfirmation, session: AsyncSession = Depends(get_db)
):
    try:
        return await confirm_payment(session, payment_id, payload)
    except HTTPException:
        raise


@router.post("/orders/{order_id}/cancel", response_model=SalesOrderRead)
async def cancel_sales_order(
    order_id: UUID, payload: SalesOrderAction, session: AsyncSession = Depends(get_db)
):
    return await cancel_order(session, order_id, payload.reason)


@router.post("/orders/{order_id}/ship", response_model=SalesOrderRead)
async def ship_sales_order(
    order_id: UUID, payload: SalesOrderAction, session: AsyncSession = Depends(get_db)
):
    return await ship_order(session, order_id, payload.reason)


@router.post("/orders/{order_id}/deliver", response_model=SalesOrderRead)
async def deliver_sales_order(
    order_id: UUID, payload: SalesOrderAction, session: AsyncSession = Depends(get_db)
):
    return await deliver_order(session, order_id, payload.reason)
