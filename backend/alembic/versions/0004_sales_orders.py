"""Add proforma quotations, sales orders, payments and order history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_sales_orders"
down_revision = "0003_crm_ai_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_quotes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_number", sa.String(40), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_name_snapshot", sa.String(200), nullable=False),
        sa.Column("customer_phone_snapshot", sa.String(32), nullable=True),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_code_snapshot", sa.String(40), nullable=False),
        sa.Column("warehouse_name_snapshot", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), server_default="DRAFT", nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=False),
        sa.Column("header_discount", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("discount_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("tax_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'SENT', 'CONVERTED', 'REJECTED', 'EXPIRED')", name="ck_sales_quotes_status"),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_sales_quotes_currency"),
        sa.CheckConstraint("subtotal >= 0 AND header_discount >= 0 AND discount_total >= 0 AND tax_total >= 0 AND grand_total >= 0", name="ck_sales_quotes_amounts"),
        sa.CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="ck_sales_quotes_tax_rate"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_number", name="uq_sales_quotes_quote_number"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_quotes_idempotency_key"),
    )
    op.create_index("ix_sales_quotes_customer_id", "sales_quotes", ["customer_id"])
    op.create_index("ix_sales_quotes_warehouse_id", "sales_quotes", ["warehouse_id"])
    op.create_index("ix_sales_quotes_customer_created", "sales_quotes", ["customer_id", "created_at"])
    op.create_index("ix_sales_quotes_status_created", "sales_quotes", ["status", "created_at"])

    op.create_table(
        "sales_quote_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_snapshot", sa.String(80), nullable=False),
        sa.Column("product_name_snapshot", sa.String(240), nullable=False),
        sa.Column("unit_snapshot", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("line_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("price_source", sa.String(24), server_default="CATALOG", nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sales_quote_lines_quantity"),
        sa.CheckConstraint("unit_price >= 0 AND discount_amount >= 0 AND line_total >= 0", name="ck_sales_quote_lines_amounts"),
        sa.ForeignKeyConstraint(["quote_id"], ["sales_quotes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id", "product_id", name="uq_sales_quote_lines_product"),
    )
    op.create_index("ix_sales_quote_lines_quote_id", "sales_quote_lines", ["quote_id"])

    op.create_table(
        "sales_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_number", sa.String(40), nullable=False),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_name_snapshot", sa.String(200), nullable=False),
        sa.Column("customer_phone_snapshot", sa.String(32), nullable=True),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_code_snapshot", sa.String(40), nullable=False),
        sa.Column("warehouse_name_snapshot", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), server_default="AWAITING_PAYMENT", nullable=False),
        sa.Column("payment_status", sa.String(20), server_default="UNPAID", nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=False),
        sa.Column("header_discount", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("discount_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("tax_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('AWAITING_PAYMENT', 'CONFIRMED', 'SHIPPED', 'DELIVERED', 'CANCELLED')", name="ck_sales_orders_status"),
        sa.CheckConstraint("payment_status IN ('UNPAID', 'PARTIALLY_PAID', 'PAID')", name="ck_sales_orders_payment_status"),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_sales_orders_currency"),
        sa.CheckConstraint("subtotal >= 0 AND header_discount >= 0 AND discount_total >= 0 AND tax_total >= 0 AND grand_total >= 0", name="ck_sales_orders_amounts"),
        sa.CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="ck_sales_orders_tax_rate"),
        sa.ForeignKeyConstraint(["quote_id"], ["sales_quotes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_number", name="uq_sales_orders_order_number"),
        sa.UniqueConstraint("quote_id", name="uq_sales_orders_quote"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_orders_idempotency_key"),
    )
    op.create_index("ix_sales_orders_customer_id", "sales_orders", ["customer_id"])
    op.create_index("ix_sales_orders_warehouse_id", "sales_orders", ["warehouse_id"])
    op.create_index("ix_sales_orders_customer_created", "sales_orders", ["customer_id", "created_at"])
    op.create_index("ix_sales_orders_status_created", "sales_orders", ["status", "created_at"])

    op.create_table(
        "sales_order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_snapshot", sa.String(80), nullable=False),
        sa.Column("product_name_snapshot", sa.String(240), nullable=False),
        sa.Column("unit_snapshot", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("line_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("price_source", sa.String(24), server_default="CATALOG", nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sales_order_items_quantity"),
        sa.CheckConstraint("unit_price >= 0 AND discount_amount >= 0 AND line_total >= 0", name="ck_sales_order_items_amounts"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reservation_id"], ["stock_reservations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "product_id", name="uq_sales_order_items_product"),
        sa.UniqueConstraint("reservation_id", name="uq_sales_order_items_reservation"),
    )
    op.create_index("ix_sales_order_items_order_id", "sales_order_items", ["order_id"])

    op.create_table(
        "sales_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_number", sa.String(40), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_method", sa.String(24), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("provider_reference", sa.String(160), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_sales_payments_amount_positive"),
        sa.CheckConstraint("status IN ('PENDING', 'CONFIRMED', 'REJECTED')", name="ck_sales_payments_status"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_number", name="uq_sales_payments_payment_number"),
        sa.UniqueConstraint("provider_reference", name="uq_sales_payments_provider_reference"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_payments_idempotency_key"),
    )
    op.create_index("ix_sales_payments_order_id", "sales_payments", ["order_id"])
    op.create_index("ix_sales_payments_order_status", "sales_payments", ["order_id", "status"])

    op.create_table(
        "sales_order_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["sales_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_sales_order_status_history_idempotency_key"),
    )
    op.create_index("ix_sales_order_status_history_order_id", "sales_order_status_history", ["order_id"])
    op.create_index("ix_sales_order_status_history_order_created", "sales_order_status_history", ["order_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_sales_order_status_history_order_created", table_name="sales_order_status_history")
    op.drop_index("ix_sales_order_status_history_order_id", table_name="sales_order_status_history")
    op.drop_table("sales_order_status_history")
    op.drop_index("ix_sales_payments_order_status", table_name="sales_payments")
    op.drop_index("ix_sales_payments_order_id", table_name="sales_payments")
    op.drop_table("sales_payments")
    op.drop_index("ix_sales_order_items_order_id", table_name="sales_order_items")
    op.drop_table("sales_order_items")
    op.drop_index("ix_sales_orders_status_created", table_name="sales_orders")
    op.drop_index("ix_sales_orders_customer_created", table_name="sales_orders")
    op.drop_index("ix_sales_orders_warehouse_id", table_name="sales_orders")
    op.drop_index("ix_sales_orders_customer_id", table_name="sales_orders")
    op.drop_table("sales_orders")
    op.drop_index("ix_sales_quote_lines_quote_id", table_name="sales_quote_lines")
    op.drop_table("sales_quote_lines")
    op.drop_index("ix_sales_quotes_status_created", table_name="sales_quotes")
    op.drop_index("ix_sales_quotes_customer_created", table_name="sales_quotes")
    op.drop_index("ix_sales_quotes_warehouse_id", table_name="sales_quotes")
    op.drop_index("ix_sales_quotes_customer_id", table_name="sales_quotes")
    op.drop_table("sales_quotes")
