from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.core.config import settings as runtime_settings
from app.db.session import get_db
from app.models.audit_event import AuditEvent
from app.models.employee import Employee
from app.models.system_setting import SystemSetting
from app.services.audit import add_audit_event

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Dashboard settings"],
    dependencies=[Depends(current_employee)],
)

DEFAULT_SETTINGS: dict[str, dict[str, Any]] = {
    "organization_name": {"value": "فروش‌یار", "category": "organization", "description": "نام نمایشی سازمان"},
    "organization_phone": {"value": "", "category": "organization", "description": "شماره تماس سازمان"},
    "organization_email": {"value": "", "category": "organization", "description": "ایمیل سازمان"},
    "default_currency": {"value": "IRT", "category": "business", "description": "واحد پیش‌فرض قیمت؛ IRT برای تومان و IRR برای ریال"},
    "time_zone": {"value": "Asia/Tehran", "category": "business", "description": "منطقه زمانی گزارش‌ها و عملیات"},
    "calendar_system": {"value": "JALALI", "category": "business", "description": "تقویم نمایشی پنل"},
    "default_order_prefix": {"value": "FY", "category": "sales", "description": "پیشوند پیشنهادی شماره سفارش‌های جدید"},
    "conversation_sla_minutes": {"value": 30, "category": "support", "description": "هدف زمان پاسخ اولیه بر حسب دقیقه"},
    "product_reorder_threshold": {"value": 0, "category": "inventory", "description": "آستانه عمومی هشدار موجودی؛ صفر یعنی فقط موجودی تمام‌شده"},
}


class SettingsUpdateRequest(BaseModel):
    values: dict[str, Any] = Field(min_length=1, max_length=20)


def _require_settings_reader(employee: Employee) -> None:
    if employee.role not in {"ADMIN", "MANAGER"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده تنظیمات کافی نیست")


def _require_settings_admin(employee: Employee) -> None:
    if employee.role != "ADMIN":
        raise HTTPException(status_code=403, detail="تغییر تنظیمات سازمانی فقط در اختیار مدیرکل است")


def _validate_setting(key: str, value: Any) -> Any:
    if key not in DEFAULT_SETTINGS:
        raise HTTPException(status_code=422, detail="کلید تنظیمات ناشناخته است")
    if key in {"organization_name", "organization_phone", "organization_email", "default_order_prefix"}:
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail="مقدار این تنظیم باید متن باشد")
        value = value.strip()
        if key == "organization_name" and not 2 <= len(value) <= 160:
            raise HTTPException(status_code=422, detail="نام سازمان باید بین ۲ تا ۱۶۰ نویسه باشد")
        if key == "organization_phone" and value and not 5 <= len(value) <= 32:
            raise HTTPException(status_code=422, detail="شماره تماس معتبر نیست")
        if key == "organization_email" and value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise HTTPException(status_code=422, detail="ایمیل سازمان معتبر نیست")
        if key == "default_order_prefix" and not re.fullmatch(r"[A-Za-z0-9-]{1,12}", value):
            raise HTTPException(status_code=422, detail="پیشوند سفارش باید ۱ تا ۱۲ نویسه لاتین، عدد یا خط تیره باشد")
        return value
    if key == "default_currency":
        if value not in {"IRT", "IRR"}:
            raise HTTPException(status_code=422, detail="واحد پول باید IRT (تومان) یا IRR (ریال) باشد")
        return value
    if key == "time_zone":
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail="منطقه زمانی باید متن باشد")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=422, detail="شناسه منطقه زمانی معتبر نیست")
        return value
    if key == "calendar_system":
        if value not in {"JALALI", "GREGORIAN"}:
            raise HTTPException(status_code=422, detail="تقویم باید JALALI یا GREGORIAN باشد")
        return value
    if key in {"conversation_sla_minutes", "product_reorder_threshold"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=422, detail="مقدار این تنظیم باید عدد صحیح باشد")
        lower, upper = (1, 10080) if key == "conversation_sla_minutes" else (0, 1000000)
        if not lower <= value <= upper:
            raise HTTPException(status_code=422, detail="مقدار تنظیم خارج از محدوده مجاز است")
        return value
    raise HTTPException(status_code=422, detail="نوع تنظیمات پشتیبانی نمی‌شود")


@router.get("/settings")
async def read_dashboard_settings(
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_settings_reader(employee)
    rows = list((await session.scalars(select(SystemSetting))).all())
    stored = {row.key: row for row in rows if row.key in DEFAULT_SETTINGS}
    result = {}
    for key, spec in DEFAULT_SETTINGS.items():
        row = stored.get(key)
        result[key] = {
            "value": row.value if row is not None else spec["value"],
            "category": spec["category"],
            "description": spec["description"],
            "updated_at": row.updated_at if row is not None else None,
        }
    return {
        "settings": result,
        "integrations": {
            "ai_assistant_configured": runtime_settings.llm_is_configured,
            "ai_provider_type": "openai-compatible",
            "ai_model": runtime_settings.llm_model if runtime_settings.llm_is_configured else None,
            "secrets_location": "environment-or-secret-manager",
        },
        "security_note": "کلیدهای API، گذرواژه‌ها و secretهای اتصال در این صفحه ذخیره یا نمایش داده نمی‌شوند.",
    }


@router.put("/settings")
async def update_dashboard_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_settings_admin(employee)
    validated = {key: _validate_setting(key, value) for key, value in payload.values.items()}
    current_rows = list((await session.scalars(
        select(SystemSetting).where(SystemSetting.key.in_(validated)).with_for_update()
    )).all())
    by_key = {row.key: row for row in current_rows}
    changed = []
    for key, value in validated.items():
        spec = DEFAULT_SETTINGS[key]
        row = by_key.get(key)
        previous = row.value if row is not None else spec["value"]
        if previous == value:
            continue
        if row is None:
            row = SystemSetting(key=key, category=spec["category"], value=value, description=spec["description"], updated_by_employee_id=employee.id)
            session.add(row)
        else:
            row.value = value
            row.updated_by_employee_id = employee.id
            row.updated_at = datetime.now(timezone.utc)
        add_audit_event(session, request, employee.id, "SETTING_UPDATED", "system_setting", key, {"previous": previous, "new": value})
        changed.append(key)
    await session.commit()
    return {"updated": changed, "updated_count": len(changed)}


@router.get("/audit-events")
async def list_dashboard_audit_events(
    resource_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_settings_reader(employee)
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0))
    if resource_type:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    rows = list((await session.scalars(stmt)).all())
    return [
        {"id": str(row.id), "actor_employee_id": str(row.actor_employee_id) if row.actor_employee_id else None, "event_type": row.event_type, "resource_type": row.resource_type, "resource_id": row.resource_id, "details": row.details, "request_id": row.request_id, "created_at": row.created_at}
        for row in rows
    ]
