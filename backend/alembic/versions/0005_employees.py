"""Add employee accounts for dashboard authentication.

Revision ID: 0005_employees
Revises: 0004_sales_orders
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_employees"
down_revision = "0004_sales_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(24), nullable=False, server_default="SALES"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('ADMIN', 'MANAGER', 'SALES', 'SUPPORT', 'WAREHOUSE')", name="ck_employees_role_valid"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_employees_email_lower", "employees", [sa.text("lower(email)")], unique=True)
    op.create_index("ix_employees_role_active", "employees", ["role", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_employees_role_active", table_name="employees")
    op.drop_index("uq_employees_email_lower", table_name="employees")
    op.drop_table("employees")
