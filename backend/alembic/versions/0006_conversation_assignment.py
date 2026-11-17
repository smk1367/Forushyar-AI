"""Add conversation assignment and priority workflow fields."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_conversation_assignment"
down_revision = "0005_employees"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("conversations", sa.Column("assigned_employee_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("conversations", sa.Column("priority", sa.String(length=12), server_default="NORMAL", nullable=False))
    op.add_column("conversations", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_conversations_assigned_employee_id_employees",
        "conversations", "employees", ["assigned_employee_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "ck_conversations_priority_valid", "conversations",
        "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')"
    )
    op.create_index(
        "ix_conversations_assignee_status_updated", "conversations",
        ["assigned_employee_id", "status", "updated_at"]
    )

def downgrade() -> None:
    op.drop_index("ix_conversations_assignee_status_updated", table_name="conversations")
    op.drop_constraint("ck_conversations_priority_valid", "conversations", type_="check")
    op.drop_constraint("fk_conversations_assigned_employee_id_employees", "conversations", type_="foreignkey")
    op.drop_column("conversations", "assigned_at")
    op.drop_column("conversations", "priority")
    op.drop_column("conversations", "assigned_employee_id")
