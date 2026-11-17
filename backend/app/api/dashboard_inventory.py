from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.employee import Employee
from app.models.inventory import InventoryBalance, StockMovement
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas import (
    InventoryAdjustment,
    InventoryIssue,
    InventoryReceipt,
    InventoryView,
    ProductCreate,
    ProductRead,
    StockMovementRead,
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
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError("نام و واحد کالا نباید خالی باشند")
        return self


@router.get("/products", response_model=list[ProductRead])
async def list_dashboard_products(
    q: str | None = Query(default=None, max_length=120),
    include_inactive: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, PRODUCT_READERS, "مشاهده کالاها")
    stmt = select(Product).order_by(Product.sku).limit(limit).offset(offset)
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Product.sku.ilike(pattern) | Product.name.ilike(pattern))
    return list((await session.scalars(stmt)).all())


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
    product = Product(**values)
    session.add(product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="کد کالا از قبل ثبت شده است") from exc
    await session.refresh(product)
    return product


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
    warehouse = Warehouse(
        code=payload.code.strip(),
        name=payload.name.strip(),
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
    low_stock_only: bool = False,
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
    if low_stock_only:
        result = [item for item in result if item.available <= 5]
    return result


@router.get("/inventory/movements", response_model=list[StockMovementRead])
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
