"""Add AI transcription and analysis fields to telephony calls."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_call_analysis"
down_revision = "0008_grandstream_telephony"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telephony_calls",
        sa.Column("transcript", sa.Text(),
                  server_default="", nullable=False),
    )
    op.add_column(
        "telephony_calls",
        sa.Column("analysis_summary", sa.Text(),
                  server_default="", nullable=False),
    )
    op.add_column(
        "telephony_calls",
        sa.Column(
            "analysis_data",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "telephony_calls",
        sa.Column(
            "analysis_status",
            sa.String(length=24),
            server_default="not_processed",
            nullable=False,
        ),
    )
    op.add_column(
        "telephony_calls",
        sa.Column(
            "analysis_error", sa.Text(),
            server_default="", nullable=False,
        ),
    )
    op.add_column(
        "telephony_calls",
        sa.Column(
            "analysis_processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("telephony_calls", "analysis_processed_at")
    op.drop_column("telephony_calls", "analysis_error")
    op.drop_column("telephony_calls", "analysis_status")
    op.drop_column("telephony_calls", "analysis_data")
    op.drop_column("telephony_calls", "analysis_summary")
    op.drop_column("telephony_calls", "transcript")
