from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.db.session import get_db
from app.models.employee import Employee
from app.security import hash_password, require_csrf

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Dashboard employees"],
    dependencies=[Depends(current_employee)],
)

EmployeeRole = Literal["ADMIN", "MANAGER", "SALES", "SUPPORT", "WAREHOUSE"]


class EmployeeCreateRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: EmployeeRole = "SALES"

    @model_validator(mode="after")
    def normalize_fields(self):
        self.full_name = self.full_name.strip()
        self.email = str(self.email).strip().lower()
        if len(self.full_name) < 2:
            raise ValueError("نام کارمند معتبر نیست")
        return self


class EmployeeUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    role: EmployeeRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("حداقل یک فیلد برای ویرایش ارسال کنید")
        if "full_name" in self.model_fields_set:
            if self.full_name is None or len(self.full_name.strip()) < 2:
                raise ValueError("نام کارمند معتبر نیست")
            self.full_name = self.full_name.strip()
        return self


class EmployeePasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class EmployeeRead(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str
    is_active: bool
    created_at: object
    last_login_at: object | None


def _require_admin(employee: Employee) -> None:
    if employee.role != "ADMIN":
        raise HTTPException(status_code=403, detail="این عملیات فقط در اختیار مدیرکل است")


@router.get("/employees", response_model=list[EmployeeRead])
async def list_dashboard_employees(
    q: str | None = None,
    role: EmployeeRole | None = None,
    active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    if employee.role not in {"ADMIN", "MANAGER"}:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای مشاهده کارکنان کافی نیست")
    stmt = select(Employee).order_by(Employee.full_name).limit(min(max(limit, 1), 200)).offset(max(offset, 0))
    if role:
        stmt = stmt.where(Employee.role == role)
    if active is not None:
        stmt = stmt.where(Employee.is_active.is_(active))
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Employee.full_name.ilike(pattern) | Employee.email.ilike(pattern))
    return list((await session.scalars(stmt)).all())


@router.post(
    "/employees",
    response_model=EmployeeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_dashboard_employee(
    payload: EmployeeCreateRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_admin(employee)
    created = Employee(
        full_name=payload.full_name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    session.add(created)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="این ایمیل قبلاً برای کارمند دیگری ثبت شده است") from exc
    await session.refresh(created)
    return created


@router.patch(
    "/employees/{employee_id}",
    response_model=EmployeeRead,
    dependencies=[Depends(require_csrf)],
)
async def update_dashboard_employee(
    employee_id: UUID,
    payload: EmployeeUpdateRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_admin(employee)
    target = await session.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if target is None:
        raise HTTPException(status_code=404, detail="کارمند یافت نشد")
    updates = payload.model_dump(exclude_unset=True)
    if target.id == employee.id and (updates.get("is_active") is False or updates.get("role") not in (None, "ADMIN")):
        raise HTTPException(status_code=409, detail="مدیرکل نمی‌تواند حساب یا نقش خودش را غیرفعال یا تنزل دهد")
    removing_admin = target.role == "ADMIN" and target.is_active and (
        updates.get("is_active") is False or updates.get("role") not in (None, "ADMIN")
    )
    if removing_admin:
        active_admins = await session.scalar(
            select(func.count()).select_from(Employee).where(Employee.role == "ADMIN", Employee.is_active.is_(True))
        ) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=409, detail="حداقل یک مدیرکل فعال باید در سامانه باقی بماند")
    for field_name, value in updates.items():
        setattr(target, field_name, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="تغییر اطلاعات کارمند با تعارض روبه‌رو شد") from exc
    await session.refresh(target)
    return target


@router.post(
    "/employees/{employee_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def reset_dashboard_employee_password(
    employee_id: UUID,
    payload: EmployeePasswordResetRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_admin(employee)
    target = await session.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if target is None:
        raise HTTPException(status_code=404, detail="کارمند یافت نشد")
    target.password_hash = hash_password(payload.new_password)
    await session.commit()
    return None
