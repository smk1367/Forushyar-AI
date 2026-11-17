import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import PositiveQuantity

Money = Decimal


class SalesLineInput(BaseModel):
    product_id: uuid.UUID
    quantity: PositiveQuantity
    unit_price: Money | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    discount_amount: Money = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class _SalesDocumentInput(BaseModel):
    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    currency_code: str = Field(default="IRT", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    header_discount: Money = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=120)
    lines: list[SalesLineInput] = Field(min_length=1, max_length=100)

    @field_validator("currency_code")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def reject_duplicate_products(self):
        product_ids = [line.product_id for line in self.lines]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("هر کالا باید فقط یک‌بار در اقلام سند بیاید؛ تعداد را در همان ردیف تجمیع کنید")
        return self


class SalesQuoteCreate(_SalesDocumentInput):
    expires_in_days: int = Field(default=7, ge=1, le=90)


class SalesOrderCreate(_SalesDocumentInput):
    pass


class SalesDocumentLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    sku_snapshot: str
    product_name_snapshot: str
    unit_snapshot: str
    quantity: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    line_total: Decimal
    price_source: str


class SalesQuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    quote_number: str
    customer_id: uuid.UUID
    customer_name_snapshot: str
    customer_phone_snapshot: str | None
    warehouse_id: uuid.UUID
    warehouse_code_snapshot: str
    warehouse_name_snapshot: str
    status: str
    currency_code: str
    subtotal: Decimal
    header_discount: Decimal
    discount_total: Decimal
    tax_rate: Decimal
    tax_total: Decimal
    grand_total: Decimal
    expires_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    lines: list[SalesDocumentLineRead]


class SalesPaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    payment_number: str
    order_id: uuid.UUID
    payment_method: str
    amount: Decimal
    currency_code: str
    status: str
    provider_reference: str | None
    notes: str | None
    created_at: datetime
    confirmed_at: datetime | None


class SalesOrderStatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_status: str | None
    to_status: str
    reason: str
    created_at: datetime


class SalesOrderItemRead(SalesDocumentLineRead):
    reservation_id: uuid.UUID


class SalesOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    order_number: str
    quote_id: uuid.UUID | None
    customer_id: uuid.UUID
    customer_name_snapshot: str
    customer_phone_snapshot: str | None
    warehouse_id: uuid.UUID
    warehouse_code_snapshot: str
    warehouse_name_snapshot: str
    status: str
    payment_status: str
    currency_code: str
    subtotal: Decimal
    header_discount: Decimal
    discount_total: Decimal
    tax_rate: Decimal
    tax_total: Decimal
    grand_total: Decimal
    notes: str | None
    created_at: datetime
    updated_at: datetime
    items: list[SalesOrderItemRead]
    payments: list[SalesPaymentRead]
    status_history: list[SalesOrderStatusHistoryRead]


class SalesPaymentCreate(BaseModel):
    payment_method: Literal["CASH", "BANK_TRANSFER", "CARD", "PAYMENT_GATEWAY"]
    amount: Money = Field(gt=0, max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)


class SalesPaymentConfirmation(BaseModel):
    reason: str = Field(min_length=3, max_length=240)
    provider_reference: str | None = Field(default=None, min_length=1, max_length=160)


class SalesOrderAction(BaseModel):
    reason: str = Field(min_length=3, max_length=240)
