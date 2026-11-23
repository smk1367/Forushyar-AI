from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import BackgroundTasks
from sqlalchemy import text, update
from app.services.telephony.call_analysis import process_call_analysis


import hmac
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_employee
from app.core.config import settings
from app.db.session import get_db
from app.models.customer import Customer
from app.models.employee import Employee
from app.models.telephony_call import TelephonyCall
from app.security import require_csrf
from app.services.telephony.grandstream import (
    GrandstreamTelephonyRuntime,
    normalize_recording_references,
    parse_cdr_payload,
)

router = APIRouter(prefix="/api/v1/telephony", tags=["Grandstream UCM6202"])
basic_auth = HTTPBasic(auto_error=False)


class ClickToCallRequest(BaseModel):
    target: str = Field(min_length=2, max_length=32)
    target_type: Literal["OUTBOUND", "EXTENSION"] = "OUTBOUND"


class CallOutcomeRequest(BaseModel):
    outcome: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=4000)


def _require_role(employee: Employee, allowed: set[str]) -> None:
    if employee.role not in allowed:
        raise HTTPException(status_code=403, detail="سطح دسترسی برای این عملیات کافی نیست")


def _runtime(request: Request) -> GrandstreamTelephonyRuntime:
    runtime = getattr(request.app.state, "telephony_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="سرویس تلفن آماده نیست")
    return runtime


def _call_json(call: TelephonyCall) -> dict:
    return {
        "id": str(call.id),
        "ucm_unique_id": call.ucm_unique_id,
        "customer_id": str(call.customer_id) if call.customer_id else None,
        "source_number": call.source_number,
        "destination_number": call.destination_number,
        "customer_number": call.customer_number,
        "caller_name": call.caller_name,
        "direction": call.direction,
        "extension": call.extension,
        "status": call.status,
        "disposition": call.disposition,
        "start_time": call.start_time,
        "answer_time": call.answer_time,
        "end_time": call.end_time,
        "duration_seconds": call.duration_seconds,
        "billsec_seconds": call.billsec_seconds,
        "recordings": call.recordings or [],
        "outcome": call.outcome,
        "call_notes": call.call_notes,
        "created_at": call.created_at,
    }


def _validate_target(target: str, target_type: str) -> str:
    value = target.strip()
    if not re.fullmatch(r"[0-9+*#]{2,32}", value):
        raise HTTPException(status_code=422, detail="شماره مقصد معتبر نیست")
    if target_type == "EXTENSION" and not re.fullmatch(r"[0-9]{2,8}", value):
        raise HTTPException(status_code=422, detail="داخلی مقصد باید ۲ تا ۸ رقم باشد")
    return value


async def _authenticate_cdr_webhook(
    credentials: HTTPBasicCredentials | None = Depends(basic_auth),
) -> None:
    expected_user = settings.ucm_webhook_username or ""
    expected_password = settings.ucm_webhook_password or ""
    if not expected_user or not expected_password:
        raise HTTPException(status_code=503, detail="احراز هویت webhook تماس پیکربندی نشده است")
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="اعتبارنامه CDR معتبر نیست",
            headers={"WWW-Authenticate": "Basic"},
        )
    valid_user = hmac.compare_digest(credentials.username, expected_user)
    valid_password = hmac.compare_digest(credentials.password, expected_password)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=401,
            detail="اعتبارنامه CDR معتبر نیست",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/status")
async def telephony_status(request: Request, employee: Employee = Depends(current_employee)):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    return _runtime(request).status()


@router.get("/live")
async def live_calls(request: Request, employee: Employee = Depends(current_employee)):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    runtime = _runtime(request)
    calls = runtime.live_calls()
    if employee.role == "SALES":
        if not employee.phone_extension:
            raise HTTPException(status_code=403, detail="داخلی تلفنی برای حساب شما تعریف نشده است")
        calls = [call for call in calls if call.get("extension") == employee.phone_extension]
    return {"items": calls, "updated_at": runtime.status()["live_updated_at"]}


@router.get("/calls")
async def list_calls(
    direction: Literal["inbound", "outbound", "internal"] | None = None,
    call_status: str | None = Query(default=None, alias="status", max_length=24),
    q: str | None = Query(default=None, max_length=120),
    missed_only: bool = False,
    customer_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    statement = select(TelephonyCall, Customer.full_name).outerjoin(
        Customer, Customer.id == TelephonyCall.customer_id
    ).order_by(
        TelephonyCall.start_time.desc(), TelephonyCall.id.desc()
    ).limit(limit).offset(offset)
    if direction:
        statement = statement.where(TelephonyCall.direction == direction)
    if call_status:
        statement = statement.where(TelephonyCall.status == call_status)
    if missed_only:
        statement = statement.where(
            TelephonyCall.direction == "inbound",
            TelephonyCall.status == "missed",
        )
    if customer_id:
        statement = statement.where(TelephonyCall.customer_id == customer_id)
    search = (q or "").strip()
    if search:
        pattern = "%" + search + "%"
        statement = statement.where(
            TelephonyCall.customer_number.ilike(pattern)
            | TelephonyCall.source_number.ilike(pattern)
            | TelephonyCall.destination_number.ilike(pattern)
            | TelephonyCall.caller_name.ilike(pattern)
        )
    if employee.role == "SALES":
        if not employee.phone_extension:
            raise HTTPException(status_code=403, detail="داخلی تلفنی برای حساب شما تعریف نشده است")
        statement = statement.where(TelephonyCall.extension == employee.phone_extension)
    rows = await session.execute(statement)
    items = [dict(_call_json(call), customer_name=name) for call, name in rows]
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/calls/{call_id}")
async def get_call(
    call_id: UUID,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    call = await session.get(TelephonyCall, call_id)
    if call is None or (employee.role == "SALES" and call.extension != employee.phone_extension):
        raise HTTPException(status_code=404, detail="تماس پیدا نشد")
    return _call_json(call)


@router.get("/customers/{customer_id}/calls")
async def customer_calls(
    customer_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    statement = (
        select(TelephonyCall)
        .where(TelephonyCall.customer_id == customer_id)
        .order_by(TelephonyCall.start_time.desc(), TelephonyCall.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if employee.role == "SALES":
        if not employee.phone_extension:
            raise HTTPException(status_code=403, detail="داخلی تلفنی برای حساب شما تعریف نشده است")
        statement = statement.where(TelephonyCall.extension == employee.phone_extension)
    calls = list((await session.scalars(statement)).all())
    return {"items": [_call_json(call) for call in calls], "limit": limit, "offset": offset}



@router.get("/calls/{call_id}/analysis")
async def get_call_analysis(
    call_id: UUID,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES"})
    call = await session.get(TelephonyCall, call_id)
    if call is None or (
        employee.role == "SALES"
        and call.extension != employee.phone_extension
    ):
        raise HTTPException(status_code=404, detail="تماس پیدا نشد")

    return {
        "analysis_status": call.analysis_status,
        "transcript": call.transcript,
        "summary": call.analysis_summary,
        "analysis_data": call.analysis_data or {},
        "processed_at": call.analysis_processed_at,
    }



@router.post("/calls/{call_id}/analyze", dependencies=[Depends(require_csrf)])
async def start_call_analysis(
    call_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES"})

    call = await session.get(TelephonyCall, call_id)
    if call is None or (
        employee.role == "SALES"
        and call.extension != employee.phone_extension
    ):
        raise HTTPException(status_code=404, detail="تماس پیدا نشد")

    if not normalize_recording_references(call.recordings or []):
        raise HTTPException(status_code=409, detail="فایل ضبط موجود نیست")

    if call.analysis_status == "completed":
        return {"status": "completed", "id": str(call_id)}

    if not _runtime(request).recording_configured:
        raise HTTPException(status_code=503, detail="سرویس ضبط آماده نیست")

    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(51995541)")
    )
    if not locked:
        raise HTTPException(status_code=429, detail="درخواست دیگری در حال ثبت است")

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=30)
    await session.execute(
        update(TelephonyCall)
        .where(
            TelephonyCall.analysis_status == "processing",
            TelephonyCall.analysis_processed_at < stale_before,
        )
        .values(
            analysis_status="failed",
            analysis_error="PROCESS_INTERRUPTED",
        )
    )

    active = await session.scalar(
        select(TelephonyCall.id)
        .where(TelephonyCall.analysis_status == "processing")
        .limit(1)
    )
    if active is not None:
        raise HTTPException(
            status_code=429,
            detail="یک مکالمه دیگر در حال تحلیل است",
        )

    await session.refresh(call, with_for_update=True)
    if call.analysis_status == "completed":
        return {"status": "completed", "id": str(call_id)}

    call.analysis_status = "processing"
    call.analysis_error = ""
    call.analysis_processed_at = datetime.now(timezone.utc)
    await session.commit()

    background_tasks.add_task(
        process_call_analysis, call_id, _runtime(request)
    )
    return {"status": "processing", "id": str(call_id)}


@router.post("/sync", dependencies=[Depends(require_csrf)])
async def manual_sync(
    request: Request,
    employee: Employee = Depends(current_employee),
):
    _require_role(employee, {"ADMIN", "MANAGER"})
    try:
        return await _runtime(request).sync_once()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="همگام‌سازی CDR از گرنداستریم انجام نشد") from exc


@router.post("/click-to-call", dependencies=[Depends(require_csrf)])
async def click_to_call(
    payload: ClickToCallRequest,
    request: Request,
    employee: Employee = Depends(current_employee),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    caller_extension = (employee.phone_extension or "").strip()
    if not re.fullmatch(r"[0-9]{2,8}", caller_extension):
        raise HTTPException(
            status_code=409,
            detail="داخلی تلفنی برای این کارمند ثبت نشده است؛ مدیر سامانه باید داخلی او را در مدیریت کارکنان تنظیم کند",
        )
    target = _validate_target(payload.target, payload.target_type)
    try:
        result = await _runtime(request).originate_call(
            target, caller_extension, payload.target_type
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="دستور تماس به مرکز تلفن ارسال نشد") from exc
    return {
        "ok": True,
        "caller_extension": caller_extension,
        "target": target,
        "target_type": payload.target_type,
        "pbx_status": result.get("status"),
    }


@router.post("/calls/{call_id}/outcome", dependencies=[Depends(require_csrf)])
async def update_call_outcome(
    call_id: UUID,
    payload: CallOutcomeRequest,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES"})
    call = await session.get(TelephonyCall, call_id, with_for_update=True)
    if call is None or (employee.role == "SALES" and call.extension != employee.phone_extension):
        raise HTTPException(status_code=404, detail="تماس پیدا نشد")
    call.outcome = payload.outcome.strip()
    call.call_notes = payload.notes.strip()
    await session.commit()
    return {"ok": True, "id": str(call.id), "outcome": call.outcome}


@router.get("/calls/{call_id}/recordings/{recording_index}")
async def call_recording(
    call_id: UUID,
    recording_index: int,
    request: Request,
    employee: Employee = Depends(current_employee),
    session: AsyncSession = Depends(get_db),
):
    _require_role(employee, {"ADMIN", "MANAGER", "SALES", "SUPPORT"})
    call = await session.get(TelephonyCall, call_id)
    if call is None or (employee.role == "SALES" and call.extension != employee.phone_extension):
        raise HTTPException(status_code=404, detail="تماس پیدا نشد")
    references = normalize_recording_references(call.recordings or [])
    if recording_index < 0 or recording_index >= len(references):
        raise HTTPException(status_code=404, detail="فایل ضبط این تماس پیدا نشد")
    reference = references[recording_index]
    try:
        content, media_type = await _runtime(request).fetch_recording(
            reference["filedir"], reference["filename"]
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="دریافت فایل ضبط از مرکز تلفن انجام نشد") from exc
    filename = re.sub(r"[^\w. -]", "_", reference["filename"])[:120] or "recording"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": 'inline; filename="' + filename + '"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/cdr-realtime", dependencies=[Depends(_authenticate_cdr_webhook)])
async def receive_realtime_cdr(request: Request):
    """Accept HTTP/HTTPS real-time CDR callbacks using a dedicated PBX credential."""
    raw = await request.body()
    records = parse_cdr_payload(raw, request.headers.get("content-type", ""))
    if not records:
        raise HTTPException(status_code=422, detail="ساختار CDR گرنداستریم قابل شناسایی نیست")
    try:
        processed = await _runtime(request).ingest_records(records)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="ثبت CDR در پایگاه داده انجام نشد") from exc
    return {"ok": True, "received": len(records), "upserted": processed}
