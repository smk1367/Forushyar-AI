import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.employee import Employee
from app.security import create_session_token, decode_session_token, require_csrf, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class EmployeeSessionRead(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str


async def current_employee(request: Request, session: AsyncSession = Depends(get_db)) -> Employee:
    token = request.cookies.get("forushyar_session")
    if not token:
        raise HTTPException(status_code=401, detail="ابتدا وارد شوید")
    employee_id = decode_session_token(token)
    employee = await session.get(Employee, UUID(employee_id))
    if employee is None or not employee.is_active:
        raise HTTPException(status_code=401, detail="حساب کاربری غیرفعال است")
    return employee


@router.post("/login", response_model=EmployeeSessionRead)
async def login(payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_db)):
    email = str(payload.email).strip().lower()
    employee = await session.scalar(select(Employee).where(Employee.email == email))
    if employee is None or not employee.is_active or not verify_password(payload.password, employee.password_hash):
        raise HTTPException(status_code=401, detail="ایمیل یا گذرواژه صحیح نیست")
    employee.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        "forushyar_session",
        create_session_token(str(employee.id)),
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        max_age=1800,
        path="/",
    )
    response.set_cookie(
        "forushyar_csrf", csrf_token, httponly=False,
        secure=settings.app_env == "production", samesite="strict",
        max_age=1800, path="/",
    )
    return EmployeeSessionRead(id=employee.id, full_name=employee.full_name, email=employee.email, role=employee.role)


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
async def logout(response: Response):
    response.delete_cookie("forushyar_session", path="/", httponly=True, samesite="strict")
    response.delete_cookie("forushyar_csrf", path="/", samesite="strict")


@router.get("/me", response_model=EmployeeSessionRead)
async def me(employee: Employee = Depends(current_employee)):
    return EmployeeSessionRead(id=employee.id, full_name=employee.full_name, email=employee.email, role=employee.role)
