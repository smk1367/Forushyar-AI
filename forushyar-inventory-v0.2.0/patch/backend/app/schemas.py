import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, condecimal

Quantity = condecimal(max_digits=18, decimal_places=3)
PositiveQuantity = condecimal(max_digits=18, decimal_places=3, gt=0)


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    description: str | None = None
    unit: str = Field(default="عدد", min_length=1, max_length=24)


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sku: str
    name: str
    description: str | None
    unit: str
    is_active: bool
    created_at: datetime


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    branch_name: str | None = Field(default=None, max_length=160)


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    branch_name: str | None
    is_active: bool
    created_at: datetime


class InventoryAdjustment(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity_delta: Quantity
    reason: str = Field(min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)
    notes: str | None = None


class InventoryReceipt(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: PositiveQuantity
    reason: str = Field(default="رسید ورود کالا", min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)
    reference_type: str | None = Field(default=None, max_length=40)
    reference_id: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class InventoryIssue(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: PositiveQuantity
    reason: str = Field(default="حواله خروج کالا", min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)
    reference_type: str | None = Field(default=None, max_length=40)
    reference_id: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class InventoryView(BaseModel):
    product_id: uuid.UUID
    sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    warehouse_name: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal


class StockMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    movement_type: str
    quantity_delta: Decimal
    reserved_delta: Decimal
    reason: str
    idempotency_key: str
    reference_type: str | None
    reference_id: str | None
    notes: str | None
    created_at: datetime


class StockTransferCreate(BaseModel):
    product_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    quantity: PositiveQuantity
    reason: str = Field(default="انتقال بین انبارها", min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)
    notes: str | None = None


class StockTransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    quantity: Decimal
    reason: str
    status: str
    idempotency_key: str
    notes: str | None
    created_at: datetime


class StockReservationCreate(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: PositiveQuantity
    reason: str = Field(default="رزرو برای سفارش مشتری", min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)
    reference_type: str | None = Field(default="ORDER", max_length=40)
    reference_id: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class StockReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    status: str
    reason: str
    idempotency_key: str
    reference_type: str | None
    reference_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    released_at: datetime | None
    consumed_at: datetime | None


class ReservationAction(BaseModel):
    reason: str = Field(min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)
    notes: str | None = None
