from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.inventory import InventoryBalance
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas import (
    InventoryAdjustment,
    InventoryView,
    ProductCreate,
    ProductRead,
    StockMovementRead,
    WarehouseCreate,
    WarehouseRead,
)
from app.services.inventory import apply_inventory_adjustment

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
    result = []
    for balance, product, warehouse in rows:
        result.append(
            InventoryView(
                product_id=product.id,
                sku=product.sku,
                product_name=product.name,
                warehouse_id=warehouse.id,
                warehouse_code=warehouse.code,
                warehouse_name=warehouse.name,
                on_hand=balance.on_hand,
                reserved=balance.reserved,
                available=Decimal(balance.on_hand) - Decimal(balance.reserved),
            )
        )
    return result


@router.post("/inventory/adjustments", response_model=StockMovementRead, status_code=status.HTTP_201_CREATED)
async def create_inventory_adjustment(
    payload: InventoryAdjustment,
    session: AsyncSession = Depends(get_db),
):
    return await apply_inventory_adjustment(session, payload)
