import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.inventory import InventoryBalance, StockMovement, StockReservation
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.sales_order import SalesOrder, SalesOrderEvent, SalesOrderLine
from app.models.warehouse import Warehouse
from app.schemas import SalesOrderCreate, SalesOrderRead

ZERO = Decimal("0")
ORDER_REFERENCE_TYPE = "SALES_ORDER"


def _request_hash(payload: SalesOrderCreate) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _load_order(session: AsyncSession, order: SalesOrder) -> SalesOrderRead:
    lines = list(
        (
            await session.scalars(
                select(SalesOrderLine)
                .where(SalesOrderLine.order_id == order.id)
                .order_by(SalesOrderLine.line_number)
            )
        ).all()
    )
    reservations = list(
        (
            await session.scalars(
                select(StockReservation)
                .where(
                    StockReservation.reference_type == ORDER_REFERENCE_TYPE,
                    StockReservation.reference_id == str(order.id),
                )
                .order_by(StockReservation.product_id, StockReservation.warehouse_id)
            )
        ).all()
    )
    events = list(
        (
            await session.scalars(
                select(SalesOrderEvent)
                .where(SalesOrderEvent.order_id == order.id)
                .order_by(SalesOrderEvent.created_at, SalesOrderEvent.id)
            )
        ).all()
    )

    warehouse_ids = {reservation.warehouse_id for reservation in reservations}
    warehouses = []
    if warehouse_ids:
        warehouses = list(
            (
                await session.scalars(
                    select(Warehouse).where(Warehouse.id.in_(warehouse_ids))
                )
            ).all()
        )
    warehouse_by_id = {warehouse.id: warehouse for warehouse in warehouses}

    customer_name = None
    if order.customer_id is not None:
        customer = await session.get(Customer, order.customer_id)
        customer_name = customer.full_name if customer is not None else None

    allocations_by_product: dict[uuid.UUID, list[dict]] = {}
    for reservation in reservations:
        warehouse = warehouse_by_id.get(reservation.warehouse_id)
        allocations_by_product.setdefault(reservation.product_id, []).append(
            {
                "reservation_id": reservation.id,
                "warehouse_id": reservation.warehouse_id,
                "warehouse_code": warehouse.code if warehouse else "UNKNOWN",
                "warehouse_name": warehouse.name if warehouse else "Unknown warehouse",
                "quantity": Decimal(reservation.quantity),
                "status": reservation.status,
            }
        )

    return SalesOrderRead(
        id=order.id,
        customer_id=order.customer_id,
        customer_name=customer_name,
        status=order.status,
        currency_code=order.currency_code,
        subtotal=Decimal(order.subtotal),
        lines=[
            {
                "id": line.id,
                "product_id": line.product_id,
                "sku": line.sku_snapshot,
                "product_name": line.product_name_snapshot,
                "unit": line.unit_snapshot,
                "quantity": Decimal(line.quantity),
                "unit_price": Decimal(line.unit_price),
                "currency_code": line.currency_code,
                "line_total": Decimal(line.line_total),
                "allocations": allocations_by_product.get(line.product_id, []),
            }
            for line in lines
        ],
        events=[
            {
                "id": event.id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "reason": event.reason,
                "created_at": event.created_at,
            }
            for event in events
        ],
        created_at=order.created_at,
        updated_at=order.updated_at,
        cancelled_at=order.cancelled_at,
        cancellation_reason=order.cancellation_reason,
    )


async def create_sales_order(
    session: AsyncSession,
    payload: SalesOrderCreate,
    idempotency_key: str,
) -> SalesOrderRead:
    request_hash = _request_hash(payload)
    try:
        existing = await session.scalar(
            select(SalesOrder).where(SalesOrder.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail="این Idempotency-Key قبلاً با اطلاعات سفارش دیگری استفاده شده است",
                )
            return await _load_order(session, existing)

        if payload.customer_id is not None:
            customer = await session.get(Customer, payload.customer_id)
            if customer is None:
                raise HTTPException(status_code=404, detail="مشتری یافت نشد")

        # Claim the idempotency key before taking inventory locks. Concurrent
        # retries then wait on this unique key and return the committed order,
        # rather than failing after observing the stock already reserved by it.
        order_id = uuid.uuid4()
        inserted_id = await session.scalar(
            pg_insert(SalesOrder)
            .values(
                id=order_id,
                customer_id=payload.customer_id,
                status="PENDING_PAYMENT",
                currency_code="XXX",
                subtotal=ZERO,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(SalesOrder.id)
        )
        if inserted_id is None:
            existing = await session.scalar(
                select(SalesOrder).where(SalesOrder.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise HTTPException(status_code=409, detail="ثبت تکرارپذیر سفارش هم‌زمان شد؛ دوباره تلاش کنید")
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail="این Idempotency-Key قبلاً با اطلاعات سفارش دیگری استفاده شده است",
                )
            return await _load_order(session, existing)

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
            .order_by(ProductPrice.product_id, ProductPrice.effective_from.desc())
            .with_for_update(read=True)
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
                detail="واحد پول قیمت کالاها یکسان نیست؛ جمع مبلغ سفارش ممکن نیست",
            )
        currency_code = next(iter(currencies))

        active_warehouses = list(
            (
                await session.scalars(
                    select(Warehouse)
                    .where(Warehouse.is_active.is_(True))
                    .order_by(Warehouse.code, Warehouse.id)
                )
            ).all()
        )
        warehouse_by_id = {warehouse.id: warehouse for warehouse in active_warehouses}
        for line in payload.lines:
            requested_warehouse_id = line.warehouse_id or payload.warehouse_id
            if requested_warehouse_id is not None:
                warehouse = warehouse_by_id.get(requested_warehouse_id)
                if warehouse is None:
                    raise HTTPException(status_code=404, detail="انبار انتخاب‌شده فعال یافت نشد")

        warehouse_ids = [warehouse.id for warehouse in active_warehouses]
        balances: list[InventoryBalance] = []
        if warehouse_ids:
            balances = list(
                (
                    await session.scalars(
                        select(InventoryBalance)
                        .where(
                            InventoryBalance.product_id.in_(product_ids),
                            InventoryBalance.warehouse_id.in_(warehouse_ids),
                        )
                        .order_by(InventoryBalance.product_id, InventoryBalance.warehouse_id)
                        .with_for_update()
                    )
                ).all()
            )
        balance_by_pair = {
            (balance.product_id, balance.warehouse_id): balance for balance in balances
        }

        # Build the entire allocation plan from locked rows before changing anything.
        plans = []
        for request_line in payload.lines:
            product = product_by_sku[request_line.sku]
            price = price_by_product_id[product.id]
            quantity = Decimal(request_line.quantity)
            selected_warehouse_id = request_line.warehouse_id or payload.warehouse_id

            if selected_warehouse_id is not None:
                candidates = [warehouse_by_id[selected_warehouse_id]]
            else:
                candidates = active_warehouses

            if not candidates:
                raise HTTPException(
                    status_code=409,
                    detail=f"موجودی کالای {product.sku} به‌دلیل نبود انبار فعال قابل تأیید نیست",
                )

            known = [
                (warehouse, balance_by_pair.get((product.id, warehouse.id)))
                for warehouse in candidates
            ]

            if selected_warehouse_id is not None:
                warehouse, balance = known[0]
                if balance is None:
                    raise HTTPException(
                        status_code=409,
                        detail=f"رکورد موجودی کالای {product.sku} در انبار انتخاب‌شده ثبت نشده است؛ موجودی نامشخص است",
                    )
                available = Decimal(balance.on_hand) - Decimal(balance.reserved)
                if available < quantity:
                    raise HTTPException(
                        status_code=409,
                        detail=f"موجودی آزاد کالای {product.sku} در انبار {warehouse.code} کافی نیست",
                    )
                allocations = [(warehouse, quantity)]
            else:
                sufficient_single = next(
                    (
                        (warehouse, balance)
                        for warehouse, balance in known
                        if balance is not None
                        and Decimal(balance.on_hand) - Decimal(balance.reserved) >= quantity
                    ),
                    None,
                )
                if sufficient_single is not None:
                    allocations = [(sufficient_single[0], quantity)]
                elif any(balance is None for _, balance in known):
                    raise HTTPException(
                        status_code=409,
                        detail=f"موجودی یک یا چند انبار برای کالای {product.sku} نامشخص است؛ تخصیص تجمیعی ایمن نیست",
                    )
                else:
                    available_total = sum(
                        (
                            Decimal(balance.on_hand) - Decimal(balance.reserved)
                            for _, balance in known
                        ),
                        ZERO,
                    )
                    if available_total < quantity:
                        raise HTTPException(
                            status_code=409,
                            detail=f"موجودی آزاد کالای {product.sku} در همه انبارهای معلوم کافی نیست",
                        )
                    remaining = quantity
                    allocations = []
                    for warehouse, balance in known:
                        available = Decimal(balance.on_hand) - Decimal(balance.reserved)
                        allocated = min(available, remaining)
                        if allocated > ZERO:
                            allocations.append((warehouse, allocated))
                            remaining -= allocated
                        if remaining == ZERO:
                            break

            plans.append((product, price, quantity, allocations))

        subtotal = sum(
            (Decimal(price.amount) * quantity for _, price, quantity, _ in plans),
            ZERO,
        )
        order = await session.get(SalesOrder, order_id)
        if order is None:
            raise HTTPException(status_code=500, detail="ثبت سفارش انجام شد اما بازیابی آن ناموفق بود")
        order.currency_code = currency_code
        order.subtotal = subtotal
        order.updated_at = datetime.now(timezone.utc)

        movement_rows = []
        reservation_rows = []
        line_rows = []
        for line_number, (product, price, quantity, allocations) in enumerate(plans, start=1):
            line_id = uuid.uuid4()
            line_total = Decimal(price.amount) * quantity
            line_rows.append(
                SalesOrderLine(
                    id=line_id,
                    order_id=order_id,
                    line_number=line_number,
                    product_id=product.id,
                    sku_snapshot=product.sku,
                    product_name_snapshot=product.name,
                    unit_snapshot=product.unit,
                    quantity=quantity,
                    unit_price=Decimal(price.amount),
                    currency_code=price.currency_code,
                    line_total=line_total,
                )
            )
            for warehouse, allocated_quantity in allocations:
                balance = balance_by_pair[(product.id, warehouse.id)]
                balance.reserved = Decimal(balance.reserved) + allocated_quantity

                reservation_id = uuid.uuid4()
                reservation_rows.append(
                    StockReservation(
                        id=reservation_id,
                        product_id=product.id,
                        warehouse_id=warehouse.id,
                        quantity=allocated_quantity,
                        status="ACTIVE",
                        reason="رزرو بابت سفارش فروش",
                        idempotency_key=f"SO:{order_id}:RESERVATION:{reservation_id}",
                        reference_type=ORDER_REFERENCE_TYPE,
                        reference_id=str(order_id),
                        notes=f"order_line_id={line_id}",
                    )
                )
                movement_rows.append(
                    StockMovement(
                        id=uuid.uuid4(),
                        product_id=product.id,
                        warehouse_id=warehouse.id,
                        movement_type="RESERVE",
                        quantity_delta=ZERO,
                        reserved_delta=allocated_quantity,
                        reason="رزرو بابت سفارش فروش",
                        idempotency_key=f"SO:{order_id}:RESERVE:{reservation_id}",
                        reference_type=ORDER_REFERENCE_TYPE,
                        reference_id=str(order_id),
                        notes=f"order_line_id={line_id}",
                    )
                )

        session.add_all(line_rows + reservation_rows + movement_rows)
        session.add(
            SalesOrderEvent(
                id=uuid.uuid4(),
                order_id=order_id,
                event_type="CREATED",
                from_status=None,
                to_status="PENDING_PAYMENT",
                reason="ثبت سفارش و رزرو اتمیک موجودی",
                idempotency_key=f"SO:{order_id}:CREATED",
            )
        )
        await session.flush()
        await session.commit()
        saved_order = await session.get(SalesOrder, order_id)
        if saved_order is None:
            raise HTTPException(status_code=500, detail="سفارش ثبت شد اما بازیابی آن ناموفق بود")
        return await _load_order(session, saved_order)

    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="تداخل داده هنگام ثبت سفارش؛ درخواست را با کلید جدید بررسی کنید") from exc
    except Exception:
        await session.rollback()
        raise


async def get_sales_order(session: AsyncSession, order_id: uuid.UUID) -> SalesOrderRead:
    order = await session.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="سفارش یافت نشد")
    return await _load_order(session, order)


async def cancel_sales_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    idempotency_key: str,
    reason: str,
) -> SalesOrderRead:
    try:
        order = await session.scalar(
            select(SalesOrder).where(SalesOrder.id == order_id).with_for_update()
        )
        if order is None:
            raise HTTPException(status_code=404, detail="سفارش یافت نشد")

        key_owner = await session.scalar(
            select(SalesOrder.id).where(
                SalesOrder.cancel_idempotency_key == idempotency_key,
                SalesOrder.id != order_id,
            )
        )
        if key_owner is not None:
            raise HTTPException(
                status_code=409,
                detail="این Idempotency-Key برای لغو سفارش دیگری استفاده شده است",
            )

        if order.status == "CANCELLED":
            if order.cancel_idempotency_key != idempotency_key:
                raise HTTPException(
                    status_code=409,
                    detail="این سفارش قبلاً لغو شده است",
                )
            if order.cancellation_reason != reason:
                raise HTTPException(
                    status_code=409,
                    detail="کلید لغو قبلاً با دلیل دیگری استفاده شده است",
                )
            return await _load_order(session, order)
        if order.status != "PENDING_PAYMENT":
            raise HTTPException(status_code=409, detail="وضعیت فعلی سفارش اجازه لغو نمی‌دهد")

        reservations = list(
            (
                await session.scalars(
                    select(StockReservation)
                    .where(
                        StockReservation.reference_type == ORDER_REFERENCE_TYPE,
                        StockReservation.reference_id == str(order.id),
                        StockReservation.status == "ACTIVE",
                    )
                    .order_by(
                        StockReservation.product_id,
                        StockReservation.warehouse_id,
                        StockReservation.id,
                    )
                    .with_for_update()
                )
            ).all()
        )

        balances: list[InventoryBalance] = []
        if reservations:
            product_ids = {reservation.product_id for reservation in reservations}
            warehouse_ids = {reservation.warehouse_id for reservation in reservations}
            balances = list(
                (
                    await session.scalars(
                        select(InventoryBalance)
                        .where(
                            InventoryBalance.product_id.in_(product_ids),
                            InventoryBalance.warehouse_id.in_(warehouse_ids),
                        )
                        .order_by(InventoryBalance.product_id, InventoryBalance.warehouse_id)
                        .with_for_update()
                    )
                ).all()
            )
        balance_by_pair = {
            (balance.product_id, balance.warehouse_id): balance for balance in balances
        }

        now = datetime.now(timezone.utc)
        movements = []
        for reservation in reservations:
            key = (reservation.product_id, reservation.warehouse_id)
            balance = balance_by_pair.get(key)
            quantity = Decimal(reservation.quantity)
            if balance is None or Decimal(balance.reserved) < quantity:
                raise HTTPException(
                    status_code=409,
                    detail="مانده رزروشده با اطلاعات سفارش سازگار نیست؛ آزادسازی خودکار متوقف شد",
                )

            balance.reserved = Decimal(balance.reserved) - quantity
            reservation.status = "RELEASED"
            reservation.released_at = now
            reservation.updated_at = now
            movements.append(
                StockMovement(
                    id=uuid.uuid4(),
                    product_id=reservation.product_id,
                    warehouse_id=reservation.warehouse_id,
                    movement_type="RELEASE",
                    quantity_delta=ZERO,
                    reserved_delta=-quantity,
                    reason=reason,
                    idempotency_key=f"SO:{order.id}:RELEASE:{reservation.id}",
                    reference_type=ORDER_REFERENCE_TYPE,
                    reference_id=str(order.id),
                    notes=f"reservation_id={reservation.id}",
                    created_at=now,
                )
            )

        order.status = "CANCELLED"
        order.cancel_idempotency_key = idempotency_key
        order.cancellation_reason = reason
        order.cancelled_at = now
        order.updated_at = now
        session.add_all(movements)
        session.add(
            SalesOrderEvent(
                id=uuid.uuid4(),
                order_id=order.id,
                event_type="CANCELLED",
                from_status="PENDING_PAYMENT",
                to_status="CANCELLED",
                reason=reason,
                idempotency_key=f"SO:{order.id}:CANCELLED",
                created_at=now,
            )
        )
        await session.flush()
        await session.commit()
        saved_order = await session.get(SalesOrder, order.id)
        if saved_order is None:
            raise HTTPException(status_code=500, detail="لغو سفارش ثبت شد اما بازیابی آن ناموفق بود")
        return await _load_order(session, saved_order)

    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="تداخل داده هنگام لغو سفارش") from exc
    except Exception:
        await session.rollback()
        raise
