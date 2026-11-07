import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, condecimal, field_validator

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

# CRM, product pricing and text-assistant schemas (release 0.3.0)
from typing import Literal


class ProductPriceCreate(BaseModel):
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency_code: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    source: str | None = Field(default="MANUAL", max_length=80)
    notes: str | None = None

    @field_validator("currency_code")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class ProductPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    amount: Decimal
    currency_code: str
    is_active: bool
    effective_from: datetime
    effective_to: datetime | None
    source: str | None
    notes: str | None
    created_at: datetime


class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, min_length=5, max_length=32)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = "".join(ch for ch in value if ch.isdigit() or ch == "+")
        if len(cleaned) < 5:
            raise ValueError("شماره تماس معتبر نیست")
        return cleaned


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    full_name: str
    phone: str | None
    email: str | None
    created_at: datetime


class ConversationCreate(BaseModel):
    customer_id: uuid.UUID | None = None
    channel: Literal["TEXT", "WEB", "WHATSAPP", "PHONE"] = "TEXT"
    subject: str | None = Field(default=None, max_length=240)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    customer_id: uuid.UUID | None
    channel: str
    status: str
    subject: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class ConversationMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    provider: str | None
    model: str | None
    created_at: datetime


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("متن پیام خالی است")
        return value


class AssistantChatResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    reply: str
    model: str
    grounded_product_skus: list[str]


class AssistantStatusRead(BaseModel):
    configured: bool
    provider_type: str
    model: str | None
    mode: str


class ConversationAction(BaseModel):
    reason: str = Field(min_length=3, max_length=240)
