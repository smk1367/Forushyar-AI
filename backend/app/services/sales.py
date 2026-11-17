import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.inventory import InventoryBalance, StockMovement, StockReservation
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.sales import (
    SalesOrder, SalesOrderItem, SalesOrderStatusHistory, SalesPayment,
    SalesQuote, SalesQuoteLine,
)
from app.models.warehouse import Warehouse
from app.sales_schemas import (
    SalesDocumentLineRead, SalesLineInput, SalesOrderCreate, SalesPaymentCreate,
    SalesPaymentConfirmation, SalesQuoteCreate,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_totals(
    lines: list[tuple[Decimal, Decimal, Decimal]],
    header_discount: Decimal,
    tax_rate: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return subtotal, total discount, tax and grand total using currency precision."""
    with localcontext() as context:
        context.prec = 50
        subtotal = sum((_money(quantity * unit_price) for quantity, unit_price, _ in lines), ZERO)
        line_discounts = sum((_money(discount) for _, _, discount in lines), ZERO)
        header_discount = _money(header_discount)
        if header_discount > subtotal - line_discounts:
            raise ValueError("تخفیف کل از مبلغ قابل تخفیف بیشتر است")
        discount_total = line_discounts + header_discount
        taxable = subtotal - discount_total
        tax_total = _money(taxable * Decimal(tax_rate) / Decimal("100"))
        grand_total = _money(taxable + tax_total)
        return subtotal, discount_total, tax_total, grand_total


def _fingerprint(payload: BaseModel | dict) -> str:
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _document_number(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:10].upper()}"


async def _active_customer_and_warehouse(
    session: AsyncSession, customer_id: uuid.UUID, warehouse_id: uuid.UUID
) -> tuple[Customer, Warehouse]:
    customer = await session.get(Customer, customer_id)
    warehouse = await session.get(Warehouse, warehouse_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")
    if warehouse is None or not warehouse.is_active:
        raise HTTPException(status_code=404, detail="انبار فعال یافت نشد")
    return customer, warehouse


async def _resolve_lines(
    session: AsyncSession, lines: list[SalesLineInput], currency_code: str
) -> list[dict]:
    """Resolve live product data/prices in stable product-ID order and snapshot it."""
    resolved: dict[uuid.UUID, dict] = {}
    for line in sorted(lines, key=lambda item: item.product_id.hex):
        product = await session.scalar(
            select(Product).where(Product.id == line.product_id).with_for_update()
        )
        if product is None or not product.is_active:
            raise HTTPException(status_code=404, detail=f"کالای فعال با شناسه {line.product_id} یافت نشد")

        price_source = "NEGOTIATED" if line.unit_price is not None else "CATALOG"
        if line.unit_price is not None:
            unit_price = _money(Decimal(line.unit_price))
        else:
            active_price = await session.scalar(
                select(ProductPrice).where(
                    ProductPrice.product_id == product.id,
                    ProductPrice.is_active.is_(True),
                )
            )
            if active_price is None:
                raise HTTPException(status_code=422, detail=f"برای کالای {product.sku} قیمت فعال تعریف نشده است")
            if active_price.currency_code.upper() != currency_code:
                raise HTTPException(
                    status_code=422,
                    detail=f"واحد پول قیمت کالای {product.sku} با واحد پول سند یکسان نیست",
                )
            unit_price = _money(Decimal(active_price.amount))

        quantity = Decimal(line.quantity)
        discount = _money(Decimal(line.discount_amount))
        gross = _money(quantity * unit_price)
        if discount > gross:
            raise HTTPException(status_code=422, detail=f"تخفیف قلم کالای {product.sku} از مبلغ آن بیشتر است")
        resolved[product.id] = {
            "product_id": product.id,
            "sku_snapshot": product.sku,
            "product_name_snapshot": product.name,
            "unit_snapshot": product.unit,
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_amount": discount,
            "line_total": _money(gross - discount),
            "price_source": price_source,
        }
    return [resolved[line.product_id] for line in lines]


def _line_totals(lines: list[dict], header_discount: Decimal, tax_rate: Decimal):
    try:
        return calculate_totals(
            [(line["quantity"], line["unit_price"], line["discount_amount"]) for line in lines],
            Decimal(header_discount),
            Decimal(tax_rate),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _load_quote(session: AsyncSession, quote_id: uuid.UUID) -> SalesQuote:
    quote = await session.scalar(
        select(SalesQuote).options(selectinload(SalesQuote.lines)).where(SalesQuote.id == quote_id)
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="پیش‌فاکتور یافت نشد")
    return quote


async def _load_order(session: AsyncSession, order_id: uuid.UUID) -> SalesOrder:
    order = await session.scalar(
        select(SalesOrder)
        .options(
            selectinload(SalesOrder.items),
            selectinload(SalesOrder.payments),
            selectinload(SalesOrder.status_history),
        )
        .where(SalesOrder.id == order_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="سفارش یافت نشد")
    return order


async def create_quote(session: AsyncSession, payload: SalesQuoteCreate) -> SalesQuote:
    fingerprint = _fingerprint(payload)
    existing = await session.scalar(
        select(SalesQuote).where(SalesQuote.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="کلید تکرارپذیری برای پیش‌فاکتور دیگری استفاده شده است")
        return await _load_quote(session, existing.id)

    customer, warehouse = await _active_customer_and_warehouse(session, payload.customer_id, payload.warehouse_id)
    lines = await _resolve_lines(session, payload.lines, payload.currency_code)
    subtotal, discount_total, tax_total, grand_total = _line_totals(
        lines, payload.header_discount, payload.tax_rate
    )
    now = datetime.now(timezone.utc)
    quote = SalesQuote(
        id=uuid.uuid4(),
        quote_number=_document_number("PQ"),
        customer_id=customer.id,
        customer_name_snapshot=customer.full_name,
        customer_phone_snapshot=customer.phone,
        warehouse_id=warehouse.id,
        warehouse_code_snapshot=warehouse.code,
        warehouse_name_snapshot=warehouse.name,
        status="DRAFT",
        currency_code=payload.currency_code,
        subtotal=subtotal,
        header_discount=_money(payload.header_discount),
        discount_total=discount_total,
        tax_rate=payload.tax_rate,
        tax_total=tax_total,
        grand_total=grand_total,
        expires_at=now + timedelta(days=payload.expires_in_days),
        notes=payload.notes,
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
    )
    session.add(quote)
    try:
        await session.flush()
        session.add_all([SalesQuoteLine(quote_id=quote.id, **line) for line in lines])
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        duplicate = await session.scalar(
            select(SalesQuote).where(SalesQuote.idempotency_key == payload.idempotency_key)
        )
        if duplicate is not None and duplicate.request_fingerprint == fingerprint:
            return await _load_quote(session, duplicate.id)
        raise HTTPException(status_code=409, detail="ثبت پیش‌فاکتور با تعارض داده روبه‌رو شد") from exc
    return await _load_quote(session, quote.id)


async def send_quote(session: AsyncSession, quote_id: uuid.UUID) -> SalesQuote:
    quote = await session.scalar(
        select(SalesQuote).where(SalesQuote.id == quote_id).with_for_update()
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="پیش‌فاکتور یافت نشد")
    if quote.status == "SENT":
        await session.commit()
        return await _load_quote(session, quote.id)
    if quote.status != "DRAFT":
        raise HTTPException(status_code=409, detail="وضعیت فعلی پیش‌فاکتور اجازه ارسال نمی‌دهد")
    if quote.expires_at is not None and quote.expires_at <= datetime.now(timezone.utc):
        quote.status = "EXPIRED"
        await session.commit()
        raise HTTPException(status_code=409, detail="مهلت اعتبار پیش‌فاکتور پایان یافته است")
    quote.status = "SENT"
    await session.commit()
    return await _load_quote(session, quote.id)


def _add_order_history(
    session: AsyncSession, order_id: uuid.UUID, from_status: str | None, to_status: str, reason: str
) -> None:
    session.add(
        SalesOrderStatusHistory(
            id=uuid.uuid4(),
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            reason=reason[:240],
            idempotency_key=f"ORDER:{order_id}:STATE:{to_status}",
        )
    )


async def _reserve_order_lines(
    session: AsyncSession, order: SalesOrder, lines: list[dict]
) -> None:
    product_ids = sorted([line["product_id"] for line in lines], key=lambda value: value.hex)
    await session.execute(
        pg_insert(InventoryBalance)
        .values([
            {"product_id": product_id, "warehouse_id": order.warehouse_id, "on_hand": ZERO, "reserved": ZERO}
            for product_id in product_ids
        ])
        .on_conflict_do_nothing(index_elements=["product_id", "warehouse_id"])
    )
    balances = list((await session.scalars(
        select(InventoryBalance)
        .where(
            InventoryBalance.product_id.in_(product_ids),
            InventoryBalance.warehouse_id == order.warehouse_id,
        )
        .order_by(InventoryBalance.product_id)
        .with_for_update()
    )).all())
    by_product = {balance.product_id: balance for balance in balances}
    if len(by_product) != len(product_ids):
        raise HTTPException(status_code=500, detail="قفل موجودی کالاها انجام نشد")

    for line in lines:
        balance = by_product[line["product_id"]]
        quantity = Decimal(line["quantity"])
        available = Decimal(balance.on_hand) - Decimal(balance.reserved)
        if available < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"موجودی آزاد کالای {line['sku_snapshot']} کافی نیست؛ موجودی آزاد: {available}",
            )

    for line in lines:
        product_id = line["product_id"]
        quantity = Decimal(line["quantity"])
        balance = by_product[product_id]
        balance.reserved = Decimal(balance.reserved) + quantity
        reservation_id = uuid.uuid4()
        reservation_key = f"SALES_ORDER:{order.id}:RESERVE:{product_id}"
        session.add(StockReservation(
            id=reservation_id,
            product_id=product_id,
            warehouse_id=order.warehouse_id,
            quantity=quantity,
            status="ACTIVE",
            reason=f"رزرو سفارش {order.order_number}",
            idempotency_key=reservation_key,
            reference_type="SALES_ORDER",
            reference_id=str(order.id),
            notes=order.notes,
        ))
        session.add(StockMovement(
            id=uuid.uuid4(),
            product_id=product_id,
            warehouse_id=order.warehouse_id,
            movement_type="ORDER_RESERVE",
            quantity_delta=ZERO,
            reserved_delta=quantity,
            reason=f"رزرو سفارش {order.order_number}",
            idempotency_key=f"{reservation_key}:MOVEMENT",
            reference_type="SALES_ORDER",
            reference_id=str(order.id),
            notes=order.notes,
        ))
        session.add(SalesOrderItem(
            id=uuid.uuid4(),
            order_id=order.id,
            reservation_id=reservation_id,
            **line,
        ))


async def create_order(session: AsyncSession, payload: SalesOrderCreate) -> SalesOrder:
    fingerprint = _fingerprint(payload)
    existing = await session.scalar(
        select(SalesOrder).where(SalesOrder.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="کلید تکرارپذیری برای سفارش دیگری استفاده شده است")
        return await _load_order(session, existing.id)

    customer, warehouse = await _active_customer_and_warehouse(session, payload.customer_id, payload.warehouse_id)
    lines = await _resolve_lines(session, payload.lines, payload.currency_code)
    subtotal, discount_total, tax_total, grand_total = _line_totals(
        lines, payload.header_discount, payload.tax_rate
    )
    now_free = grand_total == ZERO
    order = SalesOrder(
        id=uuid.uuid4(),
        order_number=_document_number("SO"),
        quote_id=None,
        customer_id=customer.id,
        customer_name_snapshot=customer.full_name,
        customer_phone_snapshot=customer.phone,
        warehouse_id=warehouse.id,
        warehouse_code_snapshot=warehouse.code,
        warehouse_name_snapshot=warehouse.name,
        status="CONFIRMED" if now_free else "AWAITING_PAYMENT",
        payment_status="PAID" if now_free else "UNPAID",
        currency_code=payload.currency_code,
        subtotal=subtotal,
        header_discount=_money(payload.header_discount),
        discount_total=discount_total,
        tax_rate=payload.tax_rate,
        tax_total=tax_total,
        grand_total=grand_total,
        notes=payload.notes,
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
    )
    session.add(order)
    try:
        await session.flush()
        await _reserve_order_lines(session, order, lines)
        _add_order_history(
            session, order.id, None, order.status,
            "ثبت سفارش مستقیم و رزرو اتمیک موجودی",
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        duplicate = await session.scalar(
            select(SalesOrder).where(SalesOrder.idempotency_key == payload.idempotency_key)
        )
        if duplicate is not None and duplicate.request_fingerprint == fingerprint:
            return await _load_order(session, duplicate.id)
        raise HTTPException(status_code=409, detail="ثبت سفارش با تعارض داده روبه‌رو شد؛ دوباره تلاش کنید") from exc
    return await _load_order(session, order.id)


async def convert_quote_to_order(session: AsyncSession, quote_id: uuid.UUID) -> SalesOrder:
    quote = await session.scalar(
        select(SalesQuote)
        .options(selectinload(SalesQuote.lines))
        .where(SalesQuote.id == quote_id)
        .with_for_update()
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="پیش‌فاکتور یافت نشد")
    existing = await session.scalar(select(SalesOrder).where(SalesOrder.quote_id == quote.id))
    if existing is not None:
        return await _load_order(session, existing.id)
    if quote.status != "SENT":
        raise HTTPException(status_code=409, detail="برای تبدیل، پیش‌فاکتور باید در وضعیت ارسال‌شده باشد")
    if quote.expires_at is not None and quote.expires_at <= datetime.now(timezone.utc):
        quote.status = "EXPIRED"
        await session.commit()
        raise HTTPException(status_code=409, detail="مهلت اعتبار پیش‌فاکتور پایان یافته است")

    lines = [{
        "product_id": line.product_id,
        "sku_snapshot": line.sku_snapshot,
        "product_name_snapshot": line.product_name_snapshot,
        "unit_snapshot": line.unit_snapshot,
        "quantity": Decimal(line.quantity),
        "unit_price": Decimal(line.unit_price),
        "discount_amount": Decimal(line.discount_amount),
        "line_total": Decimal(line.line_total),
        "price_source": line.price_source,
    } for line in quote.lines]
    is_free = Decimal(quote.grand_total) == ZERO
    order = SalesOrder(
        id=uuid.uuid4(),
        order_number=_document_number("SO"),
        quote_id=quote.id,
        customer_id=quote.customer_id,
        customer_name_snapshot=quote.customer_name_snapshot,
        customer_phone_snapshot=quote.customer_phone_snapshot,
        warehouse_id=quote.warehouse_id,
        warehouse_code_snapshot=quote.warehouse_code_snapshot,
        warehouse_name_snapshot=quote.warehouse_name_snapshot,
        status="CONFIRMED" if is_free else "AWAITING_PAYMENT",
        payment_status="PAID" if is_free else "UNPAID",
        currency_code=quote.currency_code,
        subtotal=quote.subtotal,
        header_discount=quote.header_discount,
        discount_total=quote.discount_total,
        tax_rate=quote.tax_rate,
        tax_total=quote.tax_total,
        grand_total=quote.grand_total,
        notes=quote.notes,
        idempotency_key=f"QUOTE-CONVERT:{quote.id}",
        request_fingerprint=_fingerprint({"quote_id": str(quote.id)}),
    )
    session.add(order)
    try:
        await session.flush()
        await _reserve_order_lines(session, order, lines)
        quote.status = "CONVERTED"
        _add_order_history(session, order.id, None, order.status, f"تبدیل از پیش‌فاکتور {quote.quote_number}")
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        duplicate = await session.scalar(select(SalesOrder).where(SalesOrder.quote_id == quote_id))
        if duplicate is not None:
            return await _load_order(session, duplicate.id)
        raise HTTPException(status_code=409, detail="تبدیل پیش‌فاکتور به سفارش با تعارض داده روبه‌رو شد") from exc
    return await _load_order(session, order.id)


async def list_quotes(
    session: AsyncSession, *, quote_status: str | None, limit: int, offset: int
) -> list[SalesQuote]:
    stmt = select(SalesQuote).options(selectinload(SalesQuote.lines)).order_by(
        SalesQuote.created_at.desc(), SalesQuote.id.desc()
    ).limit(limit).offset(offset)
    if quote_status:
        stmt = stmt.where(SalesQuote.status == quote_status)
    return list((await session.scalars(stmt)).all())


async def list_orders(
    session: AsyncSession, *, order_status: str | None, limit: int, offset: int
) -> list[SalesOrder]:
    stmt = select(SalesOrder).options(
        selectinload(SalesOrder.items),
        selectinload(SalesOrder.payments),
        selectinload(SalesOrder.status_history),
    ).order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc()).limit(limit).offset(offset)
    if order_status:
        stmt = stmt.where(SalesOrder.status == order_status)
    return list((await session.scalars(stmt)).all())


async def _locked_order(session: AsyncSession, order_id: uuid.UUID) -> SalesOrder:
    order = await session.scalar(
        select(SalesOrder)
        .options(selectinload(SalesOrder.items))
        .where(SalesOrder.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="سفارش یافت نشد")
    return order


async def _confirmed_payment_total(session: AsyncSession, order_id: uuid.UUID) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(SalesPayment.amount), 0)).where(
            SalesPayment.order_id == order_id,
            SalesPayment.status == "CONFIRMED",
        )
    )
    return Decimal(value or ZERO)


async def create_payment(
    session: AsyncSession, order_id: uuid.UUID, payload: SalesPaymentCreate
) -> SalesPayment:
    order = await _locked_order(session, order_id)
    fingerprint = _fingerprint(payload)
    existing = await session.scalar(
        select(SalesPayment).where(SalesPayment.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if existing.order_id == order_id and existing.request_fingerprint == fingerprint:
            await session.commit()
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری برای پرداخت دیگری استفاده شده است")
    if order.status in {"CANCELLED", "SHIPPED", "DELIVERED"}:
        raise HTTPException(status_code=409, detail="برای این سفارش امکان ثبت پرداخت وجود ندارد")
    reserved_total = await session.scalar(
        select(func.coalesce(func.sum(SalesPayment.amount), 0)).where(
            SalesPayment.order_id == order_id,
            SalesPayment.status.in_(["PENDING", "CONFIRMED"]),
        )
    )
    if Decimal(reserved_total or ZERO) + Decimal(payload.amount) > Decimal(order.grand_total):
        raise HTTPException(status_code=409, detail="مبلغ پرداخت از مانده قابل پرداخت سفارش بیشتر است")

    payment = SalesPayment(
        id=uuid.uuid4(),
        payment_number=_document_number("PAY"),
        order_id=order.id,
        payment_method=payload.payment_method,
        amount=_money(payload.amount),
        currency_code=order.currency_code,
        status="PENDING",
        provider_reference=None,
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
        notes=payload.notes,
    )
    session.add(payment)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        duplicate = await session.scalar(
            select(SalesPayment).where(SalesPayment.idempotency_key == payload.idempotency_key)
        )
        if duplicate is not None and duplicate.order_id == order_id and duplicate.request_fingerprint == fingerprint:
            return duplicate
        raise HTTPException(status_code=409, detail="ثبت پرداخت با تعارض داده روبه‌رو شد") from exc
    await session.refresh(payment)
    return payment


async def confirm_payment(
    session: AsyncSession, payment_id: uuid.UUID, payload: SalesPaymentConfirmation
) -> SalesPayment:
    payment_ref = await session.scalar(
        select(SalesPayment.order_id).where(SalesPayment.id == payment_id)
    )
    if payment_ref is None:
        raise HTTPException(status_code=404, detail="پرداخت یافت نشد")
    order = await _locked_order(session, payment_ref)
    payment = await session.scalar(
        select(SalesPayment).where(SalesPayment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="پرداخت یافت نشد")
    if payment.status == "CONFIRMED":
        if payment.provider_reference == payload.provider_reference:
            await session.commit()
            return payment
        raise HTTPException(status_code=409, detail="این پرداخت قبلاً با شناسه دیگری تأیید شده است")
    if payment.status != "PENDING" or order.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="وضعیت فعلی پرداخت قابل تأیید نیست")

    payment.status = "CONFIRMED"
    payment.provider_reference = payload.provider_reference
    payment.confirmed_at = datetime.now(timezone.utc)
    await session.flush()

    paid_total = await _confirmed_payment_total(session, order.id)
    if paid_total > Decimal(order.grand_total):
        raise HTTPException(status_code=409, detail="مجموع پرداخت‌های تأییدشده از مبلغ سفارش بیشتر شده است")
    old_status = order.status
    if paid_total >= Decimal(order.grand_total):
        order.payment_status = "PAID"
        if order.status == "AWAITING_PAYMENT":
            order.status = "CONFIRMED"
    else:
        order.payment_status = "PARTIALLY_PAID"
    if old_status != order.status:
        _add_order_history(session, order.id, old_status, order.status, payload.reason)
    await session.commit()
    await session.refresh(payment)
    return payment


async def _release_or_consume_stock(
    session: AsyncSession, order: SalesOrder, *, consume: bool, reason: str
) -> None:
    product_ids = sorted([item.product_id for item in order.items], key=lambda value: value.hex)
    balances = list((await session.scalars(
        select(InventoryBalance)
        .where(
            InventoryBalance.product_id.in_(product_ids),
            InventoryBalance.warehouse_id == order.warehouse_id,
        )
        .order_by(InventoryBalance.product_id)
        .with_for_update()
    )).all())
    by_product = {balance.product_id: balance for balance in balances}
    reservations = list((await session.scalars(
        select(StockReservation)
        .where(StockReservation.id.in_([item.reservation_id for item in order.items]))
        .order_by(StockReservation.product_id)
        .with_for_update()
    )).all())
    by_reservation = {reservation.id: reservation for reservation in reservations}

    for item in order.items:
        balance = by_product.get(item.product_id)
        reservation = by_reservation.get(item.reservation_id)
        quantity = Decimal(item.quantity)
        if balance is None or reservation is None or reservation.status != "ACTIVE":
            raise HTTPException(status_code=409, detail="رزرو فعال سفارش با وضعیت انبار سازگار نیست")
        if Decimal(balance.reserved) < quantity or Decimal(balance.on_hand) < quantity:
            raise HTTPException(status_code=409, detail="موجودی رزروشده برای تکمیل سفارش کافی نیست")
        balance.reserved = Decimal(balance.reserved) - quantity
        if consume:
            balance.on_hand = Decimal(balance.on_hand) - quantity
            reservation.status = "CONSUMED"
            reservation.consumed_at = datetime.now(timezone.utc)
            movement_type = "ORDER_SHIP"
            quantity_delta = -quantity
        else:
            reservation.status = "RELEASED"
            reservation.released_at = datetime.now(timezone.utc)
            movement_type = "ORDER_RELEASE"
            quantity_delta = ZERO
        session.add(StockMovement(
            id=uuid.uuid4(),
            product_id=item.product_id,
            warehouse_id=order.warehouse_id,
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            reserved_delta=-quantity,
            reason=reason[:240],
            idempotency_key=f"SALES_ORDER:{order.id}:{'SHIP' if consume else 'RELEASE'}:{item.product_id}",
            reference_type="SALES_ORDER",
            reference_id=str(order.id),
            notes=order.notes,
        ))


async def cancel_order(
    session: AsyncSession, order_id: uuid.UUID, reason: str
) -> SalesOrder:
    order = await _locked_order(session, order_id)
    if order.status == "CANCELLED":
        await session.commit()
        return await _load_order(session, order.id)
    if order.status not in {"AWAITING_PAYMENT", "CONFIRMED"}:
        raise HTTPException(status_code=409, detail="سفارش در این مرحله قابل لغو نیست")
    if order.payment_status != "UNPAID":
        raise HTTPException(status_code=409, detail="پیش از لغو سفارش دارای پرداخت، فرآیند استرداد وجه لازم است")
    pending = list((await session.scalars(
        select(SalesPayment).where(
            SalesPayment.order_id == order.id, SalesPayment.status == "PENDING"
        ).with_for_update()
    )).all())
    for payment in pending:
        payment.status = "REJECTED"
    await _release_or_consume_stock(session, order, consume=False, reason=reason)
    old_status = order.status
    order.status = "CANCELLED"
    _add_order_history(session, order.id, old_status, "CANCELLED", reason)
    await session.commit()
    return await _load_order(session, order.id)


async def ship_order(
    session: AsyncSession, order_id: uuid.UUID, reason: str
) -> SalesOrder:
    order = await _locked_order(session, order_id)
    if order.status == "SHIPPED":
        await session.commit()
        return await _load_order(session, order.id)
    if order.status != "CONFIRMED" or order.payment_status != "PAID":
        raise HTTPException(status_code=409, detail="ارسال فقط پس از تأیید کامل پرداخت سفارش مجاز است")
    await _release_or_consume_stock(session, order, consume=True, reason=reason)
    old_status = order.status
    order.status = "SHIPPED"
    _add_order_history(session, order.id, old_status, "SHIPPED", reason)
    await session.commit()
    return await _load_order(session, order.id)


async def deliver_order(
    session: AsyncSession, order_id: uuid.UUID, reason: str
) -> SalesOrder:
    order = await _locked_order(session, order_id)
    if order.status == "DELIVERED":
        await session.commit()
        return await _load_order(session, order.id)
    if order.status != "SHIPPED":
        raise HTTPException(status_code=409, detail="تحویل فقط برای سفارش ارسال‌شده مجاز است")
    old_status = order.status
    order.status = "DELIVERED"
    _add_order_history(session, order.id, old_status, "DELIVERED", reason)
    await session.commit()
    return await _load_order(session, order.id)
