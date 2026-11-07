import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import (
    InventoryBalance,
    StockMovement,
    StockReservation,
    StockTransfer,
)
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas import (
    InventoryAdjustment,
    InventoryIssue,
    InventoryReceipt,
    ReservationAction,
    StockReservationCreate,
    StockTransferCreate,
)

ZERO = Decimal("0")


async def _require_active_product_and_warehouse(
    session: AsyncSession, product_id: uuid.UUID, warehouse_id: uuid.UUID
) -> None:
    product = await session.get(Product, product_id)
    warehouse = await session.get(Warehouse, warehouse_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404, detail="کالای فعال یافت نشد")
    if warehouse is None or not warehouse.is_active:
        raise HTTPException(status_code=404, detail="انبار فعال یافت نشد")


async def _ensure_balance_locked(
    session: AsyncSession, product_id: uuid.UUID, warehouse_id: uuid.UUID
) -> InventoryBalance:
    """Create a zero balance if needed, then lock it for the current transaction."""
    await session.execute(
        pg_insert(InventoryBalance)
        .values(product_id=product_id, warehouse_id=warehouse_id, on_hand=ZERO, reserved=ZERO)
        .on_conflict_do_nothing(index_elements=["product_id", "warehouse_id"])
    )
    balance = await session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.product_id == product_id,
            InventoryBalance.warehouse_id == warehouse_id,
        )
        .with_for_update()
    )
    if balance is None:
        raise HTTPException(status_code=500, detail="قفل‌کردن رکورد موجودی ناموفق بود")
    return balance


def _movement_matches(
    movement: StockMovement,
    *,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    movement_type: str,
    quantity_delta: Decimal,
    reserved_delta: Decimal,
    reason: str,
    reference_type: str | None,
    reference_id: str | None,
    notes: str | None,
) -> bool:
    return (
        movement.product_id == product_id
        and movement.warehouse_id == warehouse_id
        and movement.movement_type == movement_type
        and Decimal(movement.quantity_delta) == quantity_delta
        and Decimal(movement.reserved_delta) == reserved_delta
        and movement.reason == reason
        and movement.reference_type == reference_type
        and movement.reference_id == reference_id
        and movement.notes == notes
    )


async def _apply_inventory_event(
    session: AsyncSession,
    *,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    movement_type: str,
    quantity_delta: Decimal,
    reserved_delta: Decimal = ZERO,
    reason: str,
    idempotency_key: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    notes: str | None = None,
    require_available_for_issue: bool = False,
) -> StockMovement:
    if quantity_delta == ZERO and reserved_delta == ZERO:
        raise HTTPException(status_code=422, detail="رویداد انبار باید مقدار غیرصفر داشته باشد")

    existing = await session.scalar(
        select(StockMovement).where(StockMovement.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if _movement_matches(
            existing,
            product_id=product_id,
            warehouse_id=warehouse_id,
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            reserved_delta=reserved_delta,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes,
        ):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای عملیات دیگری استفاده شده است")

    await _require_active_product_and_warehouse(session, product_id, warehouse_id)
    balance = await _ensure_balance_locked(session, product_id, warehouse_id)
    current_on_hand = Decimal(balance.on_hand)
    current_reserved = Decimal(balance.reserved)
    new_on_hand = current_on_hand + quantity_delta
    new_reserved = current_reserved + reserved_delta

    if new_on_hand < ZERO:
        raise HTTPException(status_code=409, detail="موجودی فیزیکی انبار کافی نیست")
    if new_reserved < ZERO:
        raise HTTPException(status_code=409, detail="موجودی رزروشده نمی‌تواند منفی شود")
    if new_reserved > new_on_hand:
        raise HTTPException(status_code=409, detail="موجودی قابل رزرو کافی نیست")
    if require_available_for_issue and -quantity_delta > current_on_hand - current_reserved:
        raise HTTPException(status_code=409, detail="موجودی آزاد برای خروج کالا کافی نیست")

    movement_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(StockMovement)
        .values(
            id=movement_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            reserved_delta=reserved_delta,
            reason=reason,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(StockMovement.id)
    )
    if inserted_id is None:
        await session.rollback()
        raced = await session.scalar(
            select(StockMovement).where(StockMovement.idempotency_key == idempotency_key)
        )
        if raced is not None and _movement_matches(
            raced,
            product_id=product_id,
            warehouse_id=warehouse_id,
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            reserved_delta=reserved_delta,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes,
        ):
            return raced
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری برای عملیات دیگری ثبت شده است")

    balance.on_hand = new_on_hand
    balance.reserved = new_reserved
    await session.commit()
    movement = await session.get(StockMovement, inserted_id)
    if movement is None:
        raise HTTPException(status_code=500, detail="ثبت گردش انبار انجام شد اما بازیابی آن ناموفق بود")
    return movement


async def apply_inventory_adjustment(
    session: AsyncSession, payload: InventoryAdjustment
) -> StockMovement:
    return await _apply_inventory_event(
        session,
        product_id=payload.product_id,
        warehouse_id=payload.warehouse_id,
        movement_type="ADJUSTMENT",
        quantity_delta=Decimal(payload.quantity_delta),
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        notes=payload.notes,
    )


async def receive_inventory(session: AsyncSession, payload: InventoryReceipt) -> StockMovement:
    return await _apply_inventory_event(
        session,
        product_id=payload.product_id,
        warehouse_id=payload.warehouse_id,
        movement_type="RECEIPT",
        quantity_delta=Decimal(payload.quantity),
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        notes=payload.notes,
    )


async def issue_inventory(session: AsyncSession, payload: InventoryIssue) -> StockMovement:
    return await _apply_inventory_event(
        session,
        product_id=payload.product_id,
        warehouse_id=payload.warehouse_id,
        movement_type="ISSUE",
        quantity_delta=-Decimal(payload.quantity),
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        notes=payload.notes,
        require_available_for_issue=True,
    )


async def transfer_inventory(session: AsyncSession, payload: StockTransferCreate) -> StockTransfer:
    existing = await session.scalar(
        select(StockTransfer).where(StockTransfer.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if (
            existing.product_id == payload.product_id
            and existing.from_warehouse_id == payload.from_warehouse_id
            and existing.to_warehouse_id == payload.to_warehouse_id
            and Decimal(existing.quantity) == Decimal(payload.quantity)
            and existing.reason == payload.reason
            and existing.notes == payload.notes
        ):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای انتقال دیگری استفاده شده است")

    if payload.from_warehouse_id == payload.to_warehouse_id:
        raise HTTPException(status_code=422, detail="انبار مبدأ و مقصد باید متفاوت باشند")

    await _require_active_product_and_warehouse(session, payload.product_id, payload.from_warehouse_id)
    destination = await session.get(Warehouse, payload.to_warehouse_id)
    if destination is None or not destination.is_active:
        raise HTTPException(status_code=404, detail="انبار مقصد فعال یافت نشد")

    transfer_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(StockTransfer)
        .values(
            id=transfer_id,
            product_id=payload.product_id,
            from_warehouse_id=payload.from_warehouse_id,
            to_warehouse_id=payload.to_warehouse_id,
            quantity=Decimal(payload.quantity),
            reason=payload.reason,
            status="COMPLETED",
            idempotency_key=payload.idempotency_key,
            notes=payload.notes,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(StockTransfer.id)
    )
    if inserted_id is None:
        await session.rollback()
        existing = await session.scalar(
            select(StockTransfer).where(StockTransfer.idempotency_key == payload.idempotency_key)
        )
        if existing is not None and (
            existing.product_id == payload.product_id
            and existing.from_warehouse_id == payload.from_warehouse_id
            and existing.to_warehouse_id == payload.to_warehouse_id
            and Decimal(existing.quantity) == Decimal(payload.quantity)
            and existing.reason == payload.reason
            and existing.notes == payload.notes
        ):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای انتقال دیگری استفاده شده است")

    # Insert potential zero balances first, then lock in UUID order to avoid deadlocks
    # when opposite-direction transfers happen concurrently.
    warehouse_ids = sorted(
        [payload.from_warehouse_id, payload.to_warehouse_id], key=lambda value: value.hex
    )
    await session.execute(
        pg_insert(InventoryBalance)
        .values(
            [
                {"product_id": payload.product_id, "warehouse_id": warehouse_id, "on_hand": ZERO, "reserved": ZERO}
                for warehouse_id in warehouse_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["product_id", "warehouse_id"])
    )
    balances = list(
        (
            await session.scalars(
                select(InventoryBalance)
                .where(
                    InventoryBalance.product_id == payload.product_id,
                    InventoryBalance.warehouse_id.in_(warehouse_ids),
                )
                .order_by(InventoryBalance.warehouse_id)
                .with_for_update()
            )
        ).all()
    )
    balance_by_warehouse = {balance.warehouse_id: balance for balance in balances}
    source = balance_by_warehouse.get(payload.from_warehouse_id)
    target = balance_by_warehouse.get(payload.to_warehouse_id)
    if source is None or target is None:
        raise HTTPException(status_code=500, detail="قفل موجودی انبارهای انتقال ناموفق بود")

    quantity = Decimal(payload.quantity)
    available = Decimal(source.on_hand) - Decimal(source.reserved)
    if available < quantity:
        raise HTTPException(status_code=409, detail="موجودی آزاد انبار مبدأ برای انتقال کافی نیست")

    source.on_hand = Decimal(source.on_hand) - quantity
    target.on_hand = Decimal(target.on_hand) + quantity
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            StockMovement(
                id=uuid.uuid4(),
                product_id=payload.product_id,
                warehouse_id=payload.from_warehouse_id,
                movement_type="TRANSFER_OUT",
                quantity_delta=-quantity,
                reserved_delta=ZERO,
                reason=payload.reason,
                idempotency_key=f"TRANSFER:{transfer_id}:OUT",
                reference_type="TRANSFER",
                reference_id=str(transfer_id),
                notes=payload.notes,
                created_at=now,
            ),
            StockMovement(
                id=uuid.uuid4(),
                product_id=payload.product_id,
                warehouse_id=payload.to_warehouse_id,
                movement_type="TRANSFER_IN",
                quantity_delta=quantity,
                reserved_delta=ZERO,
                reason=payload.reason,
                idempotency_key=f"TRANSFER:{transfer_id}:IN",
                reference_type="TRANSFER",
                reference_id=str(transfer_id),
                notes=payload.notes,
                created_at=now,
            ),
        ]
    )
    await session.commit()
    transfer = await session.get(StockTransfer, transfer_id)
    if transfer is None:
        raise HTTPException(status_code=500, detail="انتقال ثبت شد اما بازیابی آن ناموفق بود")
    return transfer


async def create_reservation(
    session: AsyncSession, payload: StockReservationCreate
) -> StockReservation:
    quantity = Decimal(payload.quantity)

    def matches(existing: StockReservation) -> bool:
        return (
            existing.product_id == payload.product_id
            and existing.warehouse_id == payload.warehouse_id
            and Decimal(existing.quantity) == quantity
            and existing.reason == payload.reason
            and existing.reference_type == payload.reference_type
            and existing.reference_id == payload.reference_id
            and existing.notes == payload.notes
        )

    existing = await session.scalar(
        select(StockReservation).where(StockReservation.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if matches(existing):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای رزرو دیگری استفاده شده است")

    await _require_active_product_and_warehouse(session, payload.product_id, payload.warehouse_id)
    balance = await _ensure_balance_locked(session, payload.product_id, payload.warehouse_id)

    # Recheck after acquiring the balance lock. This makes concurrent retries
    # of the same reservation idempotent before testing the reduced availability.
    existing = await session.scalar(
        select(StockReservation).where(StockReservation.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if matches(existing):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای رزرو دیگری استفاده شده است")

    available = Decimal(balance.on_hand) - Decimal(balance.reserved)
    if available < quantity:
        raise HTTPException(status_code=409, detail="موجودی آزاد برای رزرو کافی نیست")

    reservation_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(StockReservation)
        .values(
            id=reservation_id,
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            quantity=quantity,
            status="ACTIVE",
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            reference_type=payload.reference_type,
            reference_id=payload.reference_id,
            notes=payload.notes,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(StockReservation.id)
    )
    if inserted_id is None:
        await session.rollback()
        existing = await session.scalar(
            select(StockReservation).where(StockReservation.idempotency_key == payload.idempotency_key)
        )
        if existing is not None and matches(existing):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای رزرو دیگری استفاده شده است")

    balance.reserved = Decimal(balance.reserved) + quantity
    session.add(
        StockMovement(
            id=uuid.uuid4(),
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            movement_type="RESERVE",
            quantity_delta=ZERO,
            reserved_delta=quantity,
            reason=payload.reason,
            idempotency_key=f"RESERVATION:{reservation_id}:CREATE",
            reference_type="RESERVATION",
            reference_id=str(reservation_id),
            notes=payload.notes,
        )
    )
    await session.commit()
    reservation = await session.get(StockReservation, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=500, detail="رزرو ثبت شد اما بازیابی آن ناموفق بود")
    return reservation


async def _act_on_reservation(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    payload: ReservationAction,
    *,
    consume: bool,
) -> StockReservation:
    action_type = "CONSUME" if consume else "RELEASE"

    def event_matches(event: StockMovement) -> bool:
        return (
            event.movement_type == action_type
            and event.reference_type == "RESERVATION"
            and event.reference_id == str(reservation_id)
            and event.reason == payload.reason
            and event.notes == payload.notes
        )

    existing_event = await session.scalar(
        select(StockMovement).where(StockMovement.idempotency_key == payload.idempotency_key)
    )
    if existing_event is not None:
        if event_matches(existing_event):
            reservation = await session.get(StockReservation, reservation_id)
            if reservation is not None:
                return reservation
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای عملیات دیگری استفاده شده است")

    reservation = await session.scalar(
        select(StockReservation).where(StockReservation.id == reservation_id).with_for_update()
    )
    if reservation is None:
        raise HTTPException(status_code=404, detail="رزرو کالا یافت نشد")

    # Recheck after locking the reservation to make concurrent same-key retries safe.
    existing_event = await session.scalar(
        select(StockMovement).where(StockMovement.idempotency_key == payload.idempotency_key)
    )
    if existing_event is not None:
        if event_matches(existing_event):
            return reservation
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای عملیات دیگری استفاده شده است")

    if reservation.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="این رزرو قبلاً آزاد یا مصرف شده است")

    balance = await _ensure_balance_locked(session, reservation.product_id, reservation.warehouse_id)
    quantity = Decimal(reservation.quantity)
    if Decimal(balance.reserved) < quantity:
        raise HTTPException(status_code=409, detail="مانده رزروشده با اطلاعات رزرو سازگار نیست")
    quantity_delta = -quantity if consume else ZERO
    new_on_hand = Decimal(balance.on_hand) + quantity_delta
    new_reserved = Decimal(balance.reserved) - quantity
    if new_on_hand < ZERO or new_reserved < ZERO or new_reserved > new_on_hand:
        raise HTTPException(status_code=409, detail="موجودی برای تکمیل عملیات رزرو کافی نیست")

    balance.on_hand = new_on_hand
    balance.reserved = new_reserved
    reservation.status = "CONSUMED" if consume else "RELEASED"
    timestamp = datetime.now(timezone.utc)
    if consume:
        reservation.consumed_at = timestamp
    else:
        reservation.released_at = timestamp
    reservation.updated_at = timestamp

    movement_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(StockMovement)
        .values(
            id=movement_id,
            product_id=reservation.product_id,
            warehouse_id=reservation.warehouse_id,
            movement_type=action_type,
            quantity_delta=quantity_delta,
            reserved_delta=-quantity,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            reference_type="RESERVATION",
            reference_id=str(reservation.id),
            notes=payload.notes,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(StockMovement.id)
    )
    if inserted_id is None:
        await session.rollback()
        raced = await session.scalar(
            select(StockMovement).where(StockMovement.idempotency_key == payload.idempotency_key)
        )
        if raced is not None and event_matches(raced):
            current = await session.get(StockReservation, reservation_id)
            if current is not None:
                return current
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری برای عملیات دیگری ثبت شده است")

    await session.commit()
    await session.refresh(reservation)
    return reservation


async def release_reservation(
    session: AsyncSession, reservation_id: uuid.UUID, payload: ReservationAction
) -> StockReservation:
    return await _act_on_reservation(session, reservation_id, payload, consume=False)


async def consume_reservation(
    session: AsyncSession, reservation_id: uuid.UUID, payload: ReservationAction
) -> StockReservation:
    return await _act_on_reservation(session, reservation_id, payload, consume=True)
