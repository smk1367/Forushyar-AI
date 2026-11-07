from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryBalance, StockMovement
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas import InventoryAdjustment


async def apply_inventory_adjustment(
    session: AsyncSession,
    payload: InventoryAdjustment,
) -> StockMovement:
    """Apply a signed stock adjustment atomically and record an immutable ledger row."""
    delta = Decimal(payload.quantity_delta)
    if delta == 0:
        raise HTTPException(status_code=422, detail="مقدار تغییر موجودی نمی‌تواند صفر باشد")

    product = await session.get(Product, payload.product_id)
    warehouse = await session.get(Warehouse, payload.warehouse_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404, detail="کالای فعال یافت نشد")
    if warehouse is None or not warehouse.is_active:
        raise HTTPException(status_code=404, detail="انبار فعال یافت نشد")

    # Create an empty balance row if this product has never existed in this warehouse.
    insert_balance = (
        pg_insert(InventoryBalance)
        .values(product_id=payload.product_id, warehouse_id=payload.warehouse_id, on_hand=0, reserved=0)
        .on_conflict_do_nothing(index_elements=["product_id", "warehouse_id"])
    )
    await session.execute(insert_balance)

    balance = await session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.product_id == payload.product_id,
            InventoryBalance.warehouse_id == payload.warehouse_id,
        )
        .with_for_update()
    )
    if balance is None:
        raise HTTPException(status_code=500, detail="خواندن موجودی انبار ناموفق بود")

    # Idempotency is enforced by a database unique constraint, including concurrent retries.
    existing = await session.scalar(
        select(StockMovement).where(StockMovement.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        same_request = (
            existing.product_id == payload.product_id
            and existing.warehouse_id == payload.warehouse_id
            and existing.quantity_delta == delta
            and existing.reason == payload.reason
        )
        if same_request:
            existing_id = existing.id
            await session.rollback()
            return await session.get(StockMovement, existing_id)
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری قبلاً برای درخواست دیگری استفاده شده است")

    new_on_hand = Decimal(balance.on_hand) + delta
    if new_on_hand < Decimal(balance.reserved):
        raise HTTPException(status_code=409, detail="موجودی جدید از مقدار رزروشده کمتر می‌شود")
    if new_on_hand < 0:
        raise HTTPException(status_code=409, detail="موجودی انبار نمی‌تواند منفی شود")

    movement_insert = (
        pg_insert(StockMovement)
        .values(
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            movement_type="ADJUSTMENT",
            quantity_delta=delta,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            notes=payload.notes,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(StockMovement.id)
    )
    inserted_id = await session.scalar(movement_insert)
    if inserted_id is None:
        # Another concurrent request won the idempotency-key race.
        await session.rollback()
        existing = await session.scalar(
            select(StockMovement).where(StockMovement.idempotency_key == payload.idempotency_key)
        )
        if existing is not None and (
            existing.product_id == payload.product_id
            and existing.warehouse_id == payload.warehouse_id
            and existing.quantity_delta == delta
            and existing.reason == payload.reason
        ):
            return existing
        raise HTTPException(status_code=409, detail="کلید تکرارپذیری برای درخواست دیگری ثبت شده است")

    balance.on_hand = new_on_hand
    movement = await session.get(StockMovement, inserted_id)
    await session.commit()
    await session.refresh(movement)
    return movement
