from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.customer import Customer
from app.models.inventory import InventoryBalance
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.warehouse import Warehouse
from app.schemas import QuotePreviewRequest, QuotePreviewResponse

router = APIRouter(
    prefix="/api/v1/sales",
    tags=["Sales"],
    dependencies=[Depends(require_api_key)],
)


def _line_stock_status(
    quantity: Decimal, available_values: list[Decimal | None]
) -> Literal["AVAILABLE", "INSUFFICIENT", "UNKNOWN"]:
    known_values = [value for value in available_values if value is not None]
    if any(value >= quantity for value in known_values):
        return "AVAILABLE"
    if any(value is None for value in available_values):
        return "UNKNOWN"
    if known_values:
        return "INSUFFICIENT"
    return "UNKNOWN"


@router.post("/quote-preview", response_model=QuotePreviewResponse)
async def preview_sales_quote(
    payload: QuotePreviewRequest,
    session: AsyncSession = Depends(get_db),
):
    """Build a read-only quote preview using current prices and recorded stock."""
    customer_name: str | None = None
    if payload.customer_id is not None:
        customer = await session.get(Customer, payload.customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="مشتری یافت نشد")
        customer_name = customer.full_name

    requested_skus = [line.sku for line in payload.lines]
    products = list(
        (
            await session.scalars(
                select(Product).where(
                    Product.sku.in_(requested_skus),
                    Product.is_active.is_(True),
                )
            )
        ).all()
    )
    product_by_sku = {product.sku: product for product in products}
    missing_skus = sorted(set(requested_skus) - set(product_by_sku))
    if missing_skus:
        raise HTTPException(
            status_code=404,
            detail="کالای فعال با این کدها یافت نشد: " + "، ".join(missing_skus),
        )

    product_ids = [product.id for product in products]
    evaluated_at = datetime.now(timezone.utc)
    price_stmt = (
        select(ProductPrice)
        .where(
            ProductPrice.product_id.in_(product_ids),
            ProductPrice.is_active.is_(True),
            ProductPrice.effective_from <= evaluated_at,
            or_(
                ProductPrice.effective_to.is_(None),
                ProductPrice.effective_to > evaluated_at,
            ),
        )
        .order_by(ProductPrice.effective_from.desc())
    )
    prices = list((await session.scalars(price_stmt)).all())
    price_by_product_id = {price.product_id: price for price in prices}
    missing_prices = [
        product.sku for product in products if product.id not in price_by_product_id
    ]
    if missing_prices:
        raise HTTPException(
            status_code=409,
            detail="برای این کالاها قیمت معتبر و جاری ثبت نشده است: "
            + "، ".join(sorted(missing_prices)),
        )

    currencies = {price_by_product_id[product.id].currency_code for product in products}
    if len(currencies) != 1:
        raise HTTPException(
            status_code=409,
            detail="واحد پول قیمت کالاها یکسان نیست؛ جمع مبلغ پیش‌فاکتور ممکن نیست",
        )
    currency_code = next(iter(currencies))

    if payload.warehouse_id is not None:
        warehouse = await session.get(Warehouse, payload.warehouse_id)
        if warehouse is None or not warehouse.is_active:
            raise HTTPException(status_code=404, detail="انبار فعال یافت نشد")
        warehouses = [warehouse]
    else:
        warehouses = list(
            (
                await session.scalars(
                    select(Warehouse)
                    .where(Warehouse.is_active.is_(True))
                    .order_by(Warehouse.code)
                )
            ).all()
        )

    warehouse_ids = [warehouse.id for warehouse in warehouses]
    balances: list[InventoryBalance] = []
    if warehouse_ids:
        balances = list(
            (
                await session.scalars(
                    select(InventoryBalance).where(
                        InventoryBalance.product_id.in_(product_ids),
                        InventoryBalance.warehouse_id.in_(warehouse_ids),
                    )
                )
            ).all()
        )
    balance_by_pair = {
        (balance.product_id, balance.warehouse_id): balance for balance in balances
    }

    response_lines = []
    for requested_line in payload.lines:
        product = product_by_sku[requested_line.sku]
        price = price_by_product_id[product.id]
        quantity = Decimal(requested_line.quantity)
        availability = []
        available_values: list[Decimal | None] = []

        for warehouse in warehouses:
            balance = balance_by_pair.get((product.id, warehouse.id))
            if balance is None:
                on_hand = reserved = available = None
            else:
                on_hand = Decimal(balance.on_hand)
                reserved = Decimal(balance.reserved)
                available = on_hand - reserved
            available_values.append(available)
            availability.append(
                {
                    "warehouse_id": warehouse.id,
                    "warehouse_code": warehouse.code,
                    "warehouse_name": warehouse.name,
                    "on_hand": on_hand,
                    "reserved": reserved,
                    "available": available,
                }
            )

        unit_price = Decimal(price.amount)
        response_lines.append(
            {
                "product_id": product.id,
                "sku": product.sku,
                "product_name": product.name,
                "unit": product.unit,
                "quantity": quantity,
                "unit_price": unit_price,
                "currency_code": price.currency_code,
                "line_total": unit_price * quantity,
                "stock_status": _line_stock_status(quantity, available_values),
                "availability": availability,
            }
        )

    line_statuses = {line["stock_status"] for line in response_lines}
    if "INSUFFICIENT" in line_statuses:
        fulfillment_status = "INSUFFICIENT"
    elif "UNKNOWN" in line_statuses:
        fulfillment_status = "UNKNOWN"
    else:
        fulfillment_status = "AVAILABLE"

    return QuotePreviewResponse(
        evaluated_at=evaluated_at,
        customer_id=payload.customer_id,
        customer_name=customer_name,
        warehouse_id=payload.warehouse_id,
        currency_code=currency_code,
        line_count=len(response_lines),
        subtotal=sum((line["line_total"] for line in response_lines), Decimal("0")),
        fulfillment_status=fulfillment_status,
        lines=response_lines,
    )
