from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SalesQuote(Base):
    __tablename__ = "sales_quotes"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT', 'SENT', 'CONVERTED', 'REJECTED', 'EXPIRED')", name="ck_sales_quotes_status"),
        CheckConstraint("length(currency_code) = 3", name="ck_sales_quotes_currency"),
        CheckConstraint("subtotal >= 0 AND header_discount >= 0 AND discount_total >= 0 AND tax_total >= 0 AND grand_total >= 0", name="ck_sales_quotes_amounts"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="ck_sales_quotes_tax_rate"),
        Index("ix_sales_quotes_customer_created", "customer_id", "created_at"),
        Index("ix_sales_quotes_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_phone_snapshot: Mapped[str | None] = mapped_column(String(32))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_code_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    warehouse_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    header_discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    lines: Mapped[list[SalesQuoteLine]] = relationship(back_populates="quote", cascade="all, delete-orphan", order_by="SalesQuoteLine.id", lazy="selectin")


class SalesQuoteLine(Base):
    __tablename__ = "sales_quote_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_quote_lines_quantity"),
        CheckConstraint("unit_price >= 0 AND discount_amount >= 0 AND line_total >= 0", name="ck_sales_quote_lines_amounts"),
        UniqueConstraint("quote_id", "product_id", name="uq_sales_quote_lines_product"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    sku_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    unit_snapshot: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    price_source: Mapped[str] = mapped_column(String(24), nullable=False, default="CATALOG")

    quote: Mapped[SalesQuote] = relationship(back_populates="lines")


class SalesOrder(Base):
    __tablename__ = "sales_orders"
    __table_args__ = (
        CheckConstraint("status IN ('AWAITING_PAYMENT', 'CONFIRMED', 'SHIPPED', 'DELIVERED', 'CANCELLED')", name="ck_sales_orders_status"),
        CheckConstraint("payment_status IN ('UNPAID', 'PARTIALLY_PAID', 'PAID')", name="ck_sales_orders_payment_status"),
        CheckConstraint("length(currency_code) = 3", name="ck_sales_orders_currency"),
        CheckConstraint("subtotal >= 0 AND header_discount >= 0 AND discount_total >= 0 AND tax_total >= 0 AND grand_total >= 0", name="ck_sales_orders_amounts"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="ck_sales_orders_tax_rate"),
        Index("ix_sales_orders_customer_created", "customer_id", "created_at"),
        Index("ix_sales_orders_status_created", "status", "created_at"),
        UniqueConstraint("quote_id", name="uq_sales_orders_quote"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_quotes.id", ondelete="RESTRICT"))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_phone_snapshot: Mapped[str | None] = mapped_column(String(32))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True)
    warehouse_code_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    warehouse_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="AWAITING_PAYMENT")
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNPAID")
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    header_discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    items: Mapped[list[SalesOrderItem]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderItem.id", lazy="selectin")
    payments: Mapped[list[SalesPayment]] = relationship(back_populates="order", order_by="SalesPayment.created_at", lazy="selectin")
    status_history: Mapped[list[SalesOrderStatusHistory]] = relationship(back_populates="order", order_by="SalesOrderStatusHistory.created_at", lazy="selectin")


class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_order_items_quantity"),
        CheckConstraint("unit_price >= 0 AND discount_amount >= 0 AND line_total >= 0", name="ck_sales_order_items_amounts"),
        UniqueConstraint("order_id", "product_id", name="uq_sales_order_items_product"),
        UniqueConstraint("reservation_id", name="uq_sales_order_items_reservation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    reservation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_reservations.id", ondelete="RESTRICT"), nullable=False)
    sku_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    unit_snapshot: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    price_source: Mapped[str] = mapped_column(String(24), nullable=False, default="CATALOG")

    order: Mapped[SalesOrder] = relationship(back_populates="items")


class SalesPayment(Base):
    __tablename__ = "sales_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_sales_payments_amount_positive"),
        CheckConstraint("status IN ('PENDING', 'CONFIRMED', 'REJECTED')", name="ck_sales_payments_status"),
        Index("ix_sales_payments_order_status", "order_id", "status"),
        UniqueConstraint("provider_reference", name="uq_sales_payments_provider_reference"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_method: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[SalesOrder] = relationship(back_populates="payments")


class SalesOrderStatusHistory(Base):
    __tablename__ = "sales_order_status_history"
    __table_args__ = (
        Index("ix_sales_order_status_history_order_created", "order_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    order: Mapped[SalesOrder] = relationship(back_populates="status_history")
