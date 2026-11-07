"""Add customer CRM, conversations, product prices and AI text history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_crm_ai_text"
down_revision = "0002_stock_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone", name="uq_customers_phone"),
    )
    op.create_index("ix_customers_full_name", "customers", ["full_name"], unique=False)

    op.create_table(
        "product_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_product_prices_amount_nonnegative"),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_product_prices_currency_length"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_prices_product_id", "product_prices", ["product_id"], unique=False)
    op.create_index(
        "uq_product_prices_one_active_per_product", "product_prices", ["product_id"], unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.create_index("ix_product_prices_product_created", "product_prices", ["product_id", "created_at"], unique=False)

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=20), server_default="TEXT", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="OPEN", nullable=False),
        sa.Column("subject", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"], unique=False)
    op.create_index("ix_conversations_customer_updated", "conversations", ["customer_id", "updated_at"], unique=False)
    op.create_index("ix_conversations_status_updated", "conversations", ["status", "updated_at"], unique=False)

    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"], unique=False)
    op.create_index(
        "ix_conversation_messages_conversation_created", "conversation_messages",
        ["conversation_id", "created_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_conversation_created", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_conversation_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_status_updated", table_name="conversations")
    op.drop_index("ix_conversations_customer_updated", table_name="conversations")
    op.drop_index("ix_conversations_customer_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_product_prices_product_created", table_name="product_prices")
    op.drop_index("uq_product_prices_one_active_per_product", table_name="product_prices")
    op.drop_index("ix_product_prices_product_id", table_name="product_prices")
    op.drop_table("product_prices")
    op.drop_index("ix_customers_full_name", table_name="customers")
    op.drop_table("customers")
