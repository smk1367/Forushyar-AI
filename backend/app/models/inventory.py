import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryBalance(Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_inventory_on_hand_nonnegative"),
        CheckConstraint("reserved >= 0", name="ck_inventory_reserved_nonnegative"),
        CheckConstraint("on_hand >= reserved", name="ck_inventory_available_nonnegative"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), primary_key=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), primary_key=True
    )
    on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StockMovement(Base):
    """Append-only inventory ledger. quantity_delta affects physical stock;
    reserved_delta affects the reserved portion of that stock.
    """

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "quantity_delta <> 0 OR reserved_delta <> 0",
            name="ck_stock_movement_has_effect",
        ),
        Index("ix_stock_movements_product_created_at", "product_id", "created_at"),
        Index("ix_stock_movements_warehouse_created_at", "warehouse_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    movement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    reserved_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=Decimal("0"))
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StockTransfer(Base):
    __tablename__ = "stock_transfers"
    __table_args__ = (
        CheckConstraint("from_warehouse_id <> to_warehouse_id", name="ck_transfer_distinct_warehouses"),
        CheckConstraint("quantity > 0", name="ck_transfer_quantity_positive"),
        CheckConstraint("status IN ('COMPLETED', 'CANCELLED')", name="ck_transfer_status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    from_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    to_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="COMPLETED")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StockReservation(Base):
    __tablename__ = "stock_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
        CheckConstraint("status IN ('ACTIVE', 'RELEASED', 'CONSUMED')", name="ck_reservation_status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
