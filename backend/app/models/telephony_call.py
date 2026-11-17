import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelephonyCall(Base):
    """Deduplicated CDR record received from a Grandstream UCM PBX."""

    __tablename__ = "telephony_calls"
    __table_args__ = (
        Index("ix_telephony_calls_started", "start_time"),
        Index("ix_telephony_calls_direction_started", "direction", "start_time"),
        Index("ix_telephony_calls_customer_started", "customer_id", "start_time"),
        Index("ix_telephony_calls_status_started", "status", "start_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ucm_unique_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    source_number: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    destination_number: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    customer_number: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    caller_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    extension: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    disposition: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    start_time: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    answer_time: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    end_time: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billsec_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    src_trunk: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    dst_trunk: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    dst_channel: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    recordings: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    raw_cdr: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outcome: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    call_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
