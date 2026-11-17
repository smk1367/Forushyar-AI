from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent


def add_audit_event(
    session: AsyncSession,
    request: Request,
    actor_employee_id,
    event_type: str,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(AuditEvent(
        actor_employee_id=actor_employee_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        request_id=getattr(request.state, "request_id", None),
    ))
