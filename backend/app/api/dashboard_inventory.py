from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.employee import Employee
from app.models.inventory import InventoryBalance, StockMovement
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.warehouse import Warehouse
from app.schemas import (
    InventoryAdjustment,
    InventoryIssue,
    InventoryReceipt,
    InventoryView,
    ProductCreate,
    ProductRead,
    ProductPriceRead,
    StockTransferCreate,
    StockTransferRead,
    WarehouseCreate,
    WarehouseRead,
)
from app.security import require_csrf
from app.services.inventory import (
    apply_inventory_adjustment,
    issue_inventory,
    receive_inventory,
    transfer_inventory,
)

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Dashboard inventory"],
    dependencies=[Depends(current_employee)],
)


def _require_role(employee: Employee, allowed: set[str], action: str) -> None:
    if employee.role not in allowed:
        raise HTTPException(status_code=403, detail=f"سطح دسترسی برای {action} کافی نیست")


PRODUCT_READERS = {"ADMIN", "MANAGER", "SALES", "SUPPORT", "WAREHOUSE"}
STOCK_OPERATORS = {"ADMIN", "MANAGER", "WAREHOUSE"}
PRODUCT_MANAGERS = {"ADMIN", "MANAGER", "WAREHOUSE"}


class DashboardProductPriceCreate(BaseModel):
    amount_toman: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=1000)

class DashboardProductCatalogRead(BaseModel):
    id: UUID
    sku: str
    name: str
    description: str | None
    unit: str
    is_active: bool
    created_at: datetime
    current_price_toman: Decimal | None = None

class DashboardProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=24)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("حداقل یک فیلد برای ویرایش کالا ارسال کنید")
        for field_name in ("name", "unit"):
            if field_name in self.model_fields_set:
                value = getattr(self, field_name)
                if value is None or not value.strip():
                    raise ValueError("نام و واحد کالا نباید خالی باشند")
        return self


@router.get("/products", response_model=list[DashboardProductCatalogRead])
async def list_dashboard_products(
    q: str | None = Query(default=None, max_length=120),
    include_inactive: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_READERS, "مشاهده کالاها")
    stmt = (
        select(Product, ProductPrice)
        .outerjoin(
            ProductPrice,
            and_(ProductPrice.product_id == Product.id, ProductPrice.is_active.is_(True)),
        )
        .order_by(Product.sku)
        .limit(limit)
        .offset(offset)
    )
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Product.sku.ilike(pattern) | Product.name.ilike(pattern))
    rows = (await session.execute(stmt)).all()
    return [
        DashboardProductCatalogRead(
            id=product.id,
            sku=product.sku,
            name=product.name,
            description=product.description,
            unit=product.unit,
            is_active=product.is_active,
            created_at=product.created_at,
            current_price_toman=price.amount if price is not None and price.currency_code == 'IRT' else None,
        )
        for product, price in rows
    ]


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_product(
    payload: ProductCreate,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_MANAGERS, "ثبت کالا")
    values = payload.model_dump()
    values["sku"] = values["sku"].strip()
    values["name"] = values["name"].strip()
    values["unit"] = values["unit"].strip()
    if not values["sku"] or not values["name"] or not values["unit"]:
        raise HTTPException(status_code=422, detail="کد، نام و واحد کالا نباید خالی باشند")
    product = Product(**values)
    session.add(product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="کد کالا از قبل ثبت شده است") from exc
    await session.refresh(product)
    return product


@router.get("/products/{sku}/prices", response_model=list[ProductPriceRead])
async def list_dashboard_product_prices(
    sku: str,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_READERS, "مشاهده تاریخچه قیمت")
    product = await session.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    stmt = select(ProductPrice).where(ProductPrice.product_id == product.id).order_by(ProductPrice.effective_from.desc())
    return list((await session.scalars(stmt)).all())

@router.post(
    "/products/{sku}/prices",
    response_model=ProductPriceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def set_dashboard_product_price(
    sku: str,
    payload: DashboardProductPriceCreate,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER"}, "تغییر قیمت کالا")
    product = await session.scalar(select(Product).where(Product.sku == sku).with_for_update())
    if product is None:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    now = datetime.now(timezone.utc)
    active_prices = list((await session.scalars(
        select(ProductPrice).where(
            ProductPrice.product_id == product.id,
            ProductPrice.is_active.is_(True),
        ).with_for_update()
    )).all())
    for old_price in active_prices:
        old_price.is_active = False
        old_price.effective_to = now
    price = ProductPrice(
        product_id=product.id,
        amount=payload.amount_toman,
        currency_code="IRT",
        source="DASHBOARD",
        notes=payload.notes,
        is_active=True,
    )
    session.add(price)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="ثبت قیمت با تغییر هم‌زمان دیگری تداخل داشت؛ دوباره تلاش کنید") from exc
    await session.refresh(price)
    return price

@router.patch(
    "/products/{sku}",
    response_model=ProductRead,
    dependencies=[Depends(require_csrf)],
)
async def update_dashboard_product(
    sku: str,
    payload: DashboardProductUpdate,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_MANAGERS, "ویرایش کالا")
    product = await session.scalar(select(Product).where(Product.sku == sku).with_for_update())
    if product is None:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if isinstance(value, str) and field_name in {"name", "unit"}:
            value = value.strip()
        setattr(product, field_name, value)
    await session.commit()
    await session.refresh(product)
    return product


@router.get("/warehouses", response_model=list[WarehouseRead])
async def list_dashboard_warehouses(
    include_inactive: bool = False,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_READERS, "مشاهده انبارها")
    stmt = select(Warehouse).order_by(Warehouse.code)
    if not include_inactive:
        stmt = stmt.where(Warehouse.is_active.is_(True))
    return list((await session.scalars(stmt)).all())


@router.post(
    "/warehouses",
    response_model=WarehouseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_warehouse(
    payload: WarehouseCreate,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER"}, "ثبت انبار")
    code = payload.code.strip()
    name = payload.name.strip()
    if not code or not name:
        raise HTTPException(status_code=422, detail="کد و نام انبار نباید خالی باشند")
    warehouse = Warehouse(
        code=code,
        name=name,
        branch_name=payload.branch_name.strip() if payload.branch_name else None,
        is_active=True,
    )
    session.add(warehouse)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="کد انبار از قبل ثبت شده است") from exc
    await session.refresh(warehouse)
    return warehouse


@router.get("/inventory", response_model=list[InventoryView])
async def list_dashboard_inventory(
    q: str | None = Query(default=None, max_length=120),
    warehouse_id: UUID | None = None,
    out_of_stock_only: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_READERS, "مشاهده موجودی")
    stmt = (
        select(InventoryBalance, Product, Warehouse)
        .join(Product, Product.id == InventoryBalance.product_id)
        .join(Warehouse, Warehouse.id == InventoryBalance.warehouse_id)
        .where(Product.is_active.is_(True), Warehouse.is_active.is_(True))
        .order_by(Product.sku, Warehouse.code)
        .limit(limit)
        .offset(offset)
    )
    if warehouse_id:
        stmt = stmt.where(InventoryBalance.warehouse_id == warehouse_id)
    if out_of_stock_only:
        stmt = stmt.where(InventoryBalance.on_hand - InventoryBalance.reserved <= 0)
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Product.sku.ilike(pattern) | Product.name.ilike(pattern))
    rows = (await session.execute(stmt)).all()
    result = [
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
    return result


class DashboardStockMovementRead(BaseModel):
    id: UUID
    sku: str
    product_name: str
    warehouse_code: str
    warehouse_name: str
    movement_type: str
    quantity_delta: Decimal
    reserved_delta: Decimal
    reason: str
    created_at: datetime


@router.get("/inventory/movements", response_model=list[DashboardStockMovementRead])
async def list_dashboard_stock_movements(
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    movement_type: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_READERS, "مشاهده گردش انبار")
    stmt = (
        select(StockMovement, Product, Warehouse)
        .join(Product, Product.id == StockMovement.product_id)
        .join(Warehouse, Warehouse.id == StockMovement.warehouse_id)
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
    rows = (await session.execute(stmt)).all()
    return [
        DashboardStockMovementRead(
            id=movement.id,
            sku=product.sku,
            product_name=product.name,
            warehouse_code=warehouse.code,
            warehouse_name=warehouse.name,
            movement_type=movement.movement_type,
            quantity_delta=movement.quantity_delta,
            reserved_delta=movement.reserved_delta,
            reason=movement.reason,
            created_at=movement.created_at,
        )
        for movement, product, warehouse in rows
    ]


@router.post(
    "/inventory/adjustments",
    response_model=StockMovementRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_inventory_adjustment(
    payload: InventoryAdjustment,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, STOCK_OPERATORS, "تعدیل موجودی")
    return await apply_inventory_adjustment(session, payload)


@router.post(
    "/inventory/receipts",
    response_model=StockMovementRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_inventory_receipt(
    payload: InventoryReceipt,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, STOCK_OPERATORS, "ثبت رسید انبار")
    return await receive_inventory(session, payload)


@router.post(
    "/inventory/issues",
    response_model=StockMovementRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_inventory_issue(
    payload: InventoryIssue,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, STOCK_OPERATORS, "ثبت حواله انبار")
    return await issue_inventory(session, payload)


@router.post(
    "/inventory/transfers",
    response_model=StockTransferRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_inventory_transfer(
    payload: StockTransferCreate,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, STOCK_OPERATORS, "انتقال بین انبارها")
    return await transfer_inventory(session, payload)
