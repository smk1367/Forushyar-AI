from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.inventory import InventoryBalance, StockMovement, StockReservation, StockTransfer
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas import (
    InventoryAdjustment,
    InventoryIssue,
    InventoryReceipt,
    InventoryView,
    ProductCreate,
    ProductRead,
    ReservationAction,
    StockMovementRead,
    StockReservationCreate,
    StockReservationRead,
    StockTransferCreate,
    StockTransferRead,
    WarehouseCreate,
    WarehouseRead,
)
from app.services.inventory import (
    apply_inventory_adjustment,
    consume_reservation,
    create_reservation,
    issue_inventory,
    receive_inventory,
    release_reservation,
    transfer_inventory,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/products", response_model=list[ProductRead])
async def list_products(
    q: str | None = Query(default=None, max_length=120),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Product).where(Product.is_active.is_(True)).order_by(Product.sku).limit(200)
    if q:
        stmt = stmt.where(Product.sku.ilike(f"%{q}%") | Product.name.ilike(f"%{q}%"))
    return list((await session.scalars(stmt)).all())


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, session: AsyncSession = Depends(get_db)):
    product = Product(**payload.model_dump())
    session.add(product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="کد کالا تکراری است") from exc
    await session.refresh(product)
    return product


@router.get("/warehouses", response_model=list[WarehouseRead])
async def list_warehouses(session: AsyncSession = Depends(get_db)):
    stmt = select(Warehouse).where(Warehouse.is_active.is_(True)).order_by(Warehouse.code)
    return list((await session.scalars(stmt)).all())


@router.post("/warehouses", response_model=WarehouseRead, status_code=status.HTTP_201_CREATED)
async def create_warehouse(payload: WarehouseCreate, session: AsyncSession = Depends(get_db)):
    warehouse = Warehouse(**payload.model_dump())
    session.add(warehouse)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="کد انبار تکراری است") from exc
    await session.refresh(warehouse)
    return warehouse


@router.post("/inventory/adjustments", response_model=StockMovementRead, status_code=status.HTTP_201_CREATED)
async def create_inventory_adjustment(
    payload: InventoryAdjustment,
    session: AsyncSession = Depends(get_db),
):
    return await apply_inventory_adjustment(session, payload)


@router.post("/inventory/receipts", response_model=StockMovementRead, status_code=status.HTTP_201_CREATED)
async def create_inventory_receipt(
    payload: InventoryReceipt,
    session: AsyncSession = Depends(get_db),
):
    return await receive_inventory(session, payload)


@router.post("/inventory/issues", response_model=StockMovementRead, status_code=status.HTTP_201_CREATED)
async def create_inventory_issue(
    payload: InventoryIssue,
    session: AsyncSession = Depends(get_db),
):
    return await issue_inventory(session, payload)


@router.post("/inventory/transfers", response_model=StockTransferRead, status_code=status.HTTP_201_CREATED)
async def create_inventory_transfer(
    payload: StockTransferCreate,
    session: AsyncSession = Depends(get_db),
):
    return await transfer_inventory(session, payload)


@router.get("/inventory/transfers", response_model=list[StockTransferRead])
async def list_inventory_transfers(
    product_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(StockTransfer).order_by(StockTransfer.created_at.desc()).limit(limit).offset(offset)
    if product_id:
        stmt = stmt.where(StockTransfer.product_id == product_id)
    return list((await session.scalars(stmt)).all())


@router.post("/inventory/reservations", response_model=StockReservationRead, status_code=status.HTTP_201_CREATED)
async def create_inventory_reservation(
    payload: StockReservationCreate,
    session: AsyncSession = Depends(get_db),
):
    return await create_reservation(session, payload)


@router.get("/inventory/reservations", response_model=list[StockReservationRead])
async def list_inventory_reservations(
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    reservation_status: str | None = Query(default=None, alias="status", pattern="^(ACTIVE|RELEASED|CONSUMED)$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(StockReservation).order_by(StockReservation.created_at.desc()).limit(limit).offset(offset)
    if product_id:
        stmt = stmt.where(StockReservation.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockReservation.warehouse_id == warehouse_id)
    if reservation_status:
        stmt = stmt.where(StockReservation.status == reservation_status)
    return list((await session.scalars(stmt)).all())


@router.post(
    "/inventory/reservations/{reservation_id}/release",
    response_model=StockReservationRead,
)
async def release_inventory_reservation(
    reservation_id: UUID,
    payload: ReservationAction,
    session: AsyncSession = Depends(get_db),
):
    return await release_reservation(session, reservation_id, payload)


@router.post(
    "/inventory/reservations/{reservation_id}/consume",
    response_model=StockReservationRead,
)
async def consume_inventory_reservation(
    reservation_id: UUID,
    payload: ReservationAction,
    session: AsyncSession = Depends(get_db),
):
    return await consume_reservation(session, reservation_id, payload)


@router.get("/inventory/{sku}/movements", response_model=list[StockMovementRead])
async def list_product_movements(
    sku: str,
    warehouse_id: UUID | None = None,
    movement_type: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    product_id = await session.scalar(select(Product.id).where(Product.sku == sku))
    if product_id is None:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    stmt = (
        select(StockMovement)
        .where(StockMovement.product_id == product_id)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    if movement_type:
        stmt = stmt.where(StockMovement.movement_type == movement_type.upper())
    return list((await session.scalars(stmt)).all())


@router.get("/inventory/movements", response_model=list[StockMovementRead])
async def list_inventory_movements(
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    movement_type: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    stmt = (
        select(StockMovement)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if product_id:
        stmt = stmt.where(StockMovement.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    if movement_type:
        stmt = stmt.where(StockMovement.movement_type == movement_type.upper())
    return list((await session.scalars(stmt)).all())


@router.get("/inventory/{sku}", response_model=list[InventoryView])
async def get_inventory(sku: str, session: AsyncSession = Depends(get_db)):
    stmt = (
        select(InventoryBalance, Product, Warehouse)
        .join(Product, Product.id == InventoryBalance.product_id)
        .join(Warehouse, Warehouse.id == InventoryBalance.warehouse_id)
        .where(Product.sku == sku, Product.is_active.is_(True), Warehouse.is_active.is_(True))
        .order_by(Warehouse.code)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        product_exists = await session.scalar(select(Product.id).where(Product.sku == sku))
        if product_exists is None:
            raise HTTPException(status_code=404, detail="کالا یافت نشد")
    return [
        InventoryView(
            product_id=product.id,
            sku=product.sku,
            product_name=product.name,
            warehouse_id=warehouse.id,
            warehouse_code=warehouse.code,
            warehouse_name=warehouse.name,
            on_hand=balance.on_hand,
            reserved=balance.reserved,
            available=balance.on_hand - balance.reserved,
        )
        for balance, product, warehouse in rows
    ]


# Release 0.3.0: CRM, product pricing and text assistant routes.
from app.api.assistant import router as assistant_router
from app.api.crm import router as crm_router
from app.api.prices import router as prices_router

router.include_router(crm_router)
router.include_router(prices_router)
router.include_router(assistant_router)
