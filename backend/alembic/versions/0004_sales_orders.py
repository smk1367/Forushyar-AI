"""Create atomic sales orders and status history.

Orders snapshot product and price data. Inventory reservations remain in the
existing stock ledger and are linked by reference_type/reference_id.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_sales_orders"
down_revision = "0003_crm_ai_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="PENDING_PAYMENT", nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("subtotal", sa.Numeric(38, 5), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("cancel_idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'CANCELLED')",
            name="ck_sales_orders_status_valid",
        ),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_sales_orders_currency_length"),
        sa.CheckConstraint("subtotal >= 0", name="ck_sales_orders_subtotal_nonnegative"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_orders_idempotency_key"),
        sa.UniqueConstraint("cancel_idempotency_key", name="uq_sales_orders_cancel_idempotency_key"),
    )
    op.create_index(
        "ix_sales_orders_customer_created", "sales_orders", ["customer_id", "created_at"]
    )
    op.create_index("ix_sales_orders_status_created", "sales_orders", ["status", "created_at"])

    op.create_table(
        "sales_order_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=80), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=240), nullable=False),
        sa.Column("unit_snapshot", sa.String(length=24), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("line_total", sa.Numeric(38, 5), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sales_order_lines_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_sales_order_lines_unit_price_nonnegative"),
        sa.CheckConstraint("line_total >= 0", name="ck_sales_order_lines_total_nonnegative"),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_sales_order_lines_currency_length"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "line_number", name="uq_sales_order_lines_order_number"),
    )
    op.create_index("ix_sales_order_lines_product", "sales_order_lines", ["product_id"])

    op.create_table(
        "sales_order_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["sales_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_order_events_idempotency_key"),
    )
    op.create_index(
        "ix_sales_order_events_order_created", "sales_order_events", ["order_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sales_order_events_order_created", table_name="sales_order_events")
    op.drop_table("sales_order_events")
    op.drop_index("ix_sales_order_lines_product", table_name="sales_order_lines")
    op.drop_table("sales_order_lines")
    op.drop_index("ix_sales_orders_status_created", table_name="sales_orders")
    op.drop_index("ix_sales_orders_customer_created", table_name="sales_orders")
    op.drop_table("sales_orders")
