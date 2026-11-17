"""Add Grandstream UCM6202 CDR history and employee extension mapping.

Revision ID: 0008_grandstream_telephony
Revises: 0007_admin_settings_audit
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_grandstream_telephony"
down_revision = "0007_admin_settings_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("phone_extension", sa.String(length=16), nullable=True))
    op.create_index("uq_employees_phone_extension", "employees", ["phone_extension"], unique=True)

    op.create_table(
        "telephony_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ucm_unique_id", sa.String(length=255), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_number", sa.String(length=80), server_default="", nullable=False),
        sa.Column("destination_number", sa.String(length=80), server_default="", nullable=False),
        sa.Column("customer_number", sa.String(length=80), server_default="", nullable=False),
        sa.Column("caller_name", sa.String(length=200), server_default="", nullable=False),
        sa.Column("direction", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("extension", sa.String(length=24), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="unknown", nullable=False),
        sa.Column("disposition", sa.String(length=80), server_default="", nullable=False),
        sa.Column("start_time", sa.String(length=80), server_default="", nullable=False),
        sa.Column("answer_time", sa.String(length=80), server_default="", nullable=False),
        sa.Column("end_time", sa.String(length=80), server_default="", nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("billsec_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("src_trunk", sa.String(length=120), server_default="", nullable=False),
        sa.Column("dst_trunk", sa.String(length=120), server_default="", nullable=False),
        sa.Column("channel", sa.String(length=240), server_default="", nullable=False),
        sa.Column("dst_channel", sa.String(length=240), server_default="", nullable=False),
        sa.Column("recordings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("raw_cdr", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("outcome", sa.String(length=120), server_default="", nullable=False),
        sa.Column("call_notes", sa.Text(), server_default="", nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ucm_unique_id", name="uq_telephony_calls_ucm_unique_id"),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound', 'internal', 'unknown')",
            name="ck_telephony_calls_direction",
        ),
    )
    op.create_index("ix_telephony_calls_started", "telephony_calls", ["start_time"])
    op.create_index("ix_telephony_calls_direction_started", "telephony_calls", ["direction", "start_time"])
    op.create_index("ix_telephony_calls_customer_started", "telephony_calls", ["customer_id", "start_time"])
    op.create_index("ix_telephony_calls_status_started", "telephony_calls", ["status", "start_time"])


def downgrade() -> None:
    op.drop_index("ix_telephony_calls_status_started", table_name="telephony_calls")
    op.drop_index("ix_telephony_calls_customer_started", table_name="telephony_calls")
    op.drop_index("ix_telephony_calls_direction_started", table_name="telephony_calls")
    op.drop_index("ix_telephony_calls_started", table_name="telephony_calls")
    op.drop_table("telephony_calls")
    op.drop_index("uq_employees_phone_extension", table_name="employees")
    op.drop_column("employees", "phone_extension")
