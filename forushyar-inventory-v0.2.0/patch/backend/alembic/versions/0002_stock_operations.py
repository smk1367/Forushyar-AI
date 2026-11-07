"""Add stock transfer, reservation and reserved movement ledger support."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_stock_ops"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_stock_movement_nonzero", "stock_movements", type_="check")
    op.add_column(
        "stock_movements",
        sa.Column("reserved_delta", sa.Numeric(18, 3), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        "ck_stock_movement_has_effect",
        "stock_movements",
        "quantity_delta <> 0 OR reserved_delta <> 0",
    )
    op.create_index(
        "ix_stock_movements_product_created_at",
        "stock_movements",
        ["product_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_stock_movements_warehouse_created_at",
        "stock_movements",
        ["warehouse_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "stock_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="COMPLETED", nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("from_warehouse_id <> to_warehouse_id", name="ck_transfer_distinct_warehouses"),
        sa.CheckConstraint("quantity > 0", name="ck_transfer_quantity_positive"),
        sa.CheckConstraint("status IN ('COMPLETED', 'CANCELLED')", name="ck_transfer_status_valid"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_stock_transfers_idempotency_key"),
    )
    op.create_index("ix_stock_transfers_product_id", "stock_transfers", ["product_id"], unique=False)
    op.create_index("ix_stock_transfers_created_at", "stock_transfers", ["created_at"], unique=False)

    op.create_table(
        "stock_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=True),
        sa.Column("reference_id", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RELEASED', 'CONSUMED')", name="ck_reservation_status_valid"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_stock_reservations_idempotency_key"),
    )
    op.create_index("ix_stock_reservations_product_id", "stock_reservations", ["product_id"], unique=False)
    op.create_index("ix_stock_reservations_warehouse_id", "stock_reservations", ["warehouse_id"], unique=False)
    op.create_index("ix_stock_reservations_status", "stock_reservations", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stock_reservations_status", table_name="stock_reservations")
    op.drop_index("ix_stock_reservations_warehouse_id", table_name="stock_reservations")
    op.drop_index("ix_stock_reservations_product_id", table_name="stock_reservations")
    op.drop_table("stock_reservations")
    op.drop_index("ix_stock_transfers_created_at", table_name="stock_transfers")
    op.drop_index("ix_stock_transfers_product_id", table_name="stock_transfers")
    op.drop_table("stock_transfers")
    op.drop_index("ix_stock_movements_warehouse_created_at", table_name="stock_movements")
    op.drop_index("ix_stock_movements_product_created_at", table_name="stock_movements")
    op.drop_constraint("ck_stock_movement_has_effect", "stock_movements", type_="check")
    op.drop_column("stock_movements", "reserved_delta")
    op.create_check_constraint(
        "ck_stock_movement_nonzero", "stock_movements", "quantity_delta <> 0"
    )
