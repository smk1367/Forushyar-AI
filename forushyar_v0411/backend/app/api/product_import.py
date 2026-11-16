from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.inventory import InventoryBalance, StockMovement
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.warehouse import Warehouse

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
MAX_ZIP_MEMBERS = 1000
MAX_DATA_ROWS = 5000

COLUMN_HEADERS = [
    "کد کالا",
    "نام کالا",
    "توضیحات",
    "واحد",
    "قیمت (تومان)",
    "کد انبار",
    "موجودی اولیه",
    "یادداشت",
]

DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

api_router = APIRouter(
    prefix="/api/v1/products/import",
    tags=["Product Excel import"],
    dependencies=[Depends(require_api_key)],
)


class ImportConfirmRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=2, le=MAX_DATA_ROWS + 1)
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    description: str | None = None
    unit: str = Field(min_length=1, max_length=24)
    price_toman: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    warehouse_code: str | None = Field(default=None, max_length=40)
    warehouse_id: UUID | None = None
    opening_quantity: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=3)
    notes: str | None = None

    @field_validator("sku", "name", "unit")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("این فیلد نباید خالی باشد")
        return value

    @field_validator("description", "warehouse_code", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @model_validator(mode="after")
    def validate_stock_pair(self):
        if (self.warehouse_code is None) != (self.opening_quantity is None):
            raise ValueError("برای ثبت موجودی، کد انبار و موجودی اولیه باید هر دو تکمیل شوند")
        if self.opening_quantity is not None and self.warehouse_id is None:
            raise ValueError("برای ثبت موجودی، انبار مقصد باید در پیش‌نمایش انتخاب شود")
        if self.opening_quantity is None and self.warehouse_id is not None:
            raise ValueError("بدون موجودی اولیه نباید انباری انتخاب شود")
        return self


class ImportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[ImportConfirmRow] = Field(min_length=1, max_length=MAX_DATA_ROWS)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "بله" if value else "خیر"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    value = str(value).strip()
    return value or None


def _normalize_header(value: Any) -> str:
    text = str(value or "").strip().replace("ي", "ی").replace("ك", "ک")
    text = text.replace("\u200c", "").replace(" ", "")
    return text


def _decimal_value(value: Any, *, field_name: str, decimal_places: int) -> Decimal | None:
    raw = _text(value)
    if raw is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        raw = str(value)
    raw = raw.translate(DIGIT_TRANSLATION)
    raw = raw.replace("٬", "").replace(",", "").replace("٫", ".")
    raw = raw.replace(" ", "")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"مقدار «{field_name}» عددی نیست")
    if not number.is_finite():
        raise ValueError(f"مقدار «{field_name}» معتبر نیست")
    if number < 0:
        raise ValueError(f"مقدار «{field_name}» نمی‌تواند منفی باشد")
    if max(0, -number.as_tuple().exponent) > decimal_places:
        raise ValueError(f"تعداد اعشار «{field_name}» حداکثر {decimal_places} رقم است")
    if len(number.as_tuple().digits) > 18:
        raise ValueError(f"مقدار «{field_name}» بیش از حد بزرگ است")
    return number


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _validate_workbook_container(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise HTTPException(status_code=400, detail="ساختار فایل Excel معتبر نیست")
            uncompressed_size = sum(item.file_size for item in members)
            if uncompressed_size > MAX_UNCOMPRESSED_BYTES:
                raise HTTPException(status_code=400, detail="حجم بازشده فایل Excel بیش از حد مجاز است")
            for item in members:
                member_path = PurePosixPath(item.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise HTTPException(status_code=400, detail="ساختار فایل Excel ایمن نیست")
            bad_member = archive.testzip()
            if bad_member:
                raise HTTPException(status_code=400, detail="بخشی از فایل Excel خراب است")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="فایل انتخاب‌شده یک فایل معتبر XLSX نیست") from exc


def parse_workbook_bytes(data: bytes) -> list[dict[str, Any]]:
    """Parse the documented Persian XLSX layout without writing anything to the database."""
    _validate_workbook_container(data)
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except (InvalidFileException, OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="خواندن فایل Excel ممکن نشد؛ فایل را دوباره ذخیره و انتخاب کنید") from exc

    try:
        if "کالاها" not in workbook.sheetnames:
            raise HTTPException(status_code=400, detail="برگه‌ای با نام «کالاها» در فایل پیدا نشد")
        worksheet = workbook["کالاها"]
        if worksheet.max_row is not None and worksheet.max_row - 1 > MAX_DATA_ROWS:
            raise HTTPException(status_code=400, detail=f"تعداد ردیف‌های داده نباید بیشتر از {MAX_DATA_ROWS} باشد")

        header_cells = next(worksheet.iter_rows(min_row=1, max_row=1), ())
        header_positions = {
            _normalize_header(cell.value): index
            for index, cell in enumerate(header_cells)
            if cell.value is not None
        }
        missing = [
            label for label in ("کد کالا", "نام کالا", "واحد")
            if _normalize_header(label) not in header_positions
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail="ستون‌های ضروری در فایل موجود نیست: " + "، ".join(missing),
            )

        def cell_value(cells, header: str):
            position = header_positions.get(_normalize_header(header))
            if position is None or position >= len(cells):
                return None
            return cells[position].value

        parsed_rows: list[dict[str, Any]] = []
        for row_number, cells in enumerate(worksheet.iter_rows(min_row=2), start=2):
            if not any(cell.value is not None for cell in cells):
                continue

            errors: list[str] = []
            formula_columns = [
                str(cell.column) for cell in cells if getattr(cell, "data_type", None) == "f"
            ]
            if formula_columns:
                errors.append("فرمول در سلول‌های Excel مجاز نیست؛ مقدار نهایی را به‌صورت عدد یا متن وارد کنید")

            sku = _text(cell_value(cells, "کد کالا"))
            if sku:
                sku = sku.translate(DIGIT_TRANSLATION)
            name = _text(cell_value(cells, "نام کالا"))
            description = _text(cell_value(cells, "توضیحات"))
            unit = _text(cell_value(cells, "واحد"))
            warehouse_code = _text(cell_value(cells, "کد انبار"))
            if warehouse_code:
                warehouse_code = warehouse_code.translate(DIGIT_TRANSLATION)
            notes = _text(cell_value(cells, "یادداشت"))

            if not sku:
                errors.append("کد کالا الزامی است")
            elif len(sku) > 80:
                errors.append("طول کد کالا نباید بیشتر از ۸۰ نویسه باشد")
            if not name:
                errors.append("نام کالا الزامی است")
            elif len(name) > 240:
                errors.append("طول نام کالا نباید بیشتر از ۲۴۰ نویسه باشد")
            if not unit:
                errors.append("واحد کالا الزامی است")
            elif len(unit) > 24:
                errors.append("طول واحد کالا نباید بیشتر از ۲۴ نویسه باشد")
            if warehouse_code and len(warehouse_code) > 40:
                errors.append("طول کد انبار نباید بیشتر از ۴۰ نویسه باشد")

            price = None
            try:
                price = _decimal_value(cell_value(cells, "قیمت (تومان)"), field_name="قیمت (تومان)", decimal_places=2)
            except ValueError as exc:
                errors.append(str(exc))

            quantity = None
            try:
                quantity = _decimal_value(cell_value(cells, "موجودی اولیه"), field_name="موجودی اولیه", decimal_places=3)
            except ValueError as exc:
                errors.append(str(exc))

            if (warehouse_code is None) != (quantity is None):
                errors.append("برای ثبت موجودی، کد انبار و موجودی اولیه را با هم وارد کنید؛ در غیر این صورت هر دو را خالی بگذارید")

            parsed_rows.append({
                "row_number": row_number,
                "sku": sku,
                "name": name,
                "description": description,
                "unit": unit,
                "price_toman": _decimal_string(price),
                "warehouse_code": warehouse_code,
                "warehouse_id": None,
                "opening_quantity": _decimal_string(quantity),
                "notes": notes,
                "errors": errors,
            })

        if not parsed_rows:
            raise HTTPException(status_code=400, detail="در برگه «کالاها» هیچ ردیف داده‌ای وجود ندارد")

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in parsed_rows:
            if row["sku"]:
                groups[row["sku"]].append(row)

        identity_fields = ("name", "description", "unit", "price_toman")
        for sku, group in groups.items():
            if len(group) > 1:
                reference = tuple(group[0][field] for field in identity_fields)
                for row in group:
                    if tuple(row[field] for field in identity_fields) != reference:
                        row["errors"].append(
                            f"مشخصات کالا با SKU «{sku}» در تمام ردیف‌های آن باید یکسان باشد"
                        )
                    if not row["warehouse_code"] or row["opening_quantity"] is None:
                        row["errors"].append(
                            f"برای ثبت یک کالا در چند انبار، تمام ردیف‌های SKU «{sku}» باید کد انبار و موجودی داشته باشند"
                        )
                seen_warehouse_codes: set[str] = set()
                for row in group:
                    code = row["warehouse_code"]
                    if code and code in seen_warehouse_codes:
                        row["errors"].append(
                            f"کد کالا «{sku}» برای انبار «{code}» بیش از یک‌بار تکرار شده است"
                        )
                    elif code:
                        seen_warehouse_codes.add(code)

        return parsed_rows
    finally:
        workbook.close()


def _same_product_fields(rows: list[ImportConfirmRow]) -> bool:
    first = rows[0]
    return all(
        row.name == first.name
        and row.description == first.description
        and row.unit == first.unit
        and row.price_toman == first.price_toman
        for row in rows[1:]
    )


def _validate_confirm_rows(rows: list[ImportConfirmRow]) -> dict[str, list[ImportConfirmRow]]:
    groups: dict[str, list[ImportConfirmRow]] = defaultdict(list)
    for row in rows:
        groups[row.sku].append(row)

    for sku, group in groups.items():
        if len(group) > 1:
            if not _same_product_fields(group):
                lines = "، ".join(str(row.row_number) for row in group)
                raise HTTPException(
                    status_code=422,
                    detail=f"مشخصات تکراری SKU «{sku}» در ردیف‌های {lines} یکسان نیست",
                )
            if any(row.warehouse_id is None or row.opening_quantity is None for row in group):
                raise HTTPException(
                    status_code=422,
                    detail=f"برای SKU «{sku}» که چند ردیف دارد، همه ردیف‌ها باید انبار و موجودی داشته باشند",
                )

        seen_warehouses: set[UUID | None] = set()
        for row in group:
            if row.warehouse_id in seen_warehouses:
                raise HTTPException(
                    status_code=422,
                    detail=f"SKU «{sku}» برای یک انبار بیش از یک‌بار ارسال شده است (ردیف {row.row_number})",
                )
            seen_warehouses.add(row.warehouse_id)
    # One source warehouse code must map to one destination across the entire file.
    code_to_id: dict[str, UUID] = {}
    for row in rows:
        if row.warehouse_code and row.warehouse_id:
            previous = code_to_id.get(row.warehouse_code)
            if previous is not None and previous != row.warehouse_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"کد انبار فایل «{row.warehouse_code}» در چند مقصد متفاوت نگاشت شده است",
                )
            code_to_id[row.warehouse_code] = row.warehouse_id

    return groups


@api_router.get("/template")
async def download_product_template():
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "کالاها"
    worksheet.append(COLUMN_HEADERS)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = "A1:H1"
    worksheet.row_dimensions[1].height = 28
    for cell in worksheet[1]:
        cell.font = Font(name="Tahoma", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="243B53")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = [18, 28, 34, 16, 20, 18, 18, 28]
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[chr(64 + index)].width = width

    guide = workbook.create_sheet("راهنما")
    guide_rows = [
        ["راهنمای ورود گروهی کالا به فروش‌یار"],
        ["هر ردیف، اطلاعات یک کالا یا موجودی همان کالا در یک انبار را نشان می‌دهد."],
        ["اگر یک کالا در چند انبار موجودی دارد، SKU را در چند ردیف تکرار کنید و برای هر ردیف یک کد انبار متفاوت وارد کنید."],
        ["در ردیف‌های دارای SKU تکراری، نام، توضیحات، واحد و قیمت باید دقیقاً یکسان باشند."],
        ["برای هر ترکیب SKU و کد انبار فقط یک ردیف مجاز است."],
        ["برای ثبت موجودی، کد انبار و موجودی اولیه را با هم وارد کنید؛ برای ثبت کالا بدون موجودی، هر دو ستون را خالی بگذارید."],
        ["قیمت بر حسب تومان است. خالی گذاشتن قیمت یعنی قیمت ثبت نشود؛ صفر یک قیمت واقعی صفر است."],
        ["کد کالا باید قبلاً در سامانه ثبت نشده باشد؛ ورود گروهی کالاهای تکراری یا به‌روزرسانی کالاهای قبلی انجام نمی‌شود."],
        [f"حداکثر تعداد ردیف داده: {MAX_DATA_ROWS}"],
        ["ابتدا فایل را پیش‌نمایش کنید. در صورت وجود حتی یک خطا، هیچ ردیفی ثبت نخواهد شد."],
        ["کد انبار داخل Excel باید در مرحله پیش‌نمایش به یکی از انبارهای فعال فروش‌یار نگاشت شود؛ نگاشت خودکار انجام نمی‌شود."],
    ]
    for item in guide_rows:
        guide.append(item)
    guide.column_dimensions["A"].width = 115
    for row in guide.iter_rows():
        for cell in row:
            cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
            cell.font = Font(name="Tahoma", size=10, bold=(cell.row == 1))
        guide.row_dimensions[row[0].row].height = 30 if row[0].row > 1 else 26
    guide["A1"].fill = PatternFill("solid", fgColor="D9EAF7")

    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    workbook.close()
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="forushyar-products-template-v0411.xlsx"'},
    )


@api_router.post("/preview")
async def preview_product_excel(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
    filename = (file.filename or "").strip().lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="فقط فایل Excel با پسوند .xlsx پشتیبانی می‌شود")

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="حجم فایل نباید بیشتر از ۵ مگابایت باشد")

    rows = parse_workbook_bytes(data)
    skus = {row["sku"] for row in rows if row["sku"]}
    existing_skus: set[str] = set()
    if skus:
        result = await session.execute(select(Product.sku).where(Product.sku.in_(skus)))
        existing_skus = set(result.scalars().all())

    for row in rows:
        if row["sku"] in existing_skus:
            row["errors"].append(f"کد کالا «{row['sku']}» از قبل در فروش‌یار ثبت شده است")

    warehouse_codes = sorted({
        row["warehouse_code"]
        for row in rows
        if row["warehouse_code"] and row["opening_quantity"] is not None
    })
    error_rows = sum(1 for row in rows if row["errors"])
    return {
        "row_count": len(rows),
        "product_count": len({row["sku"] for row in rows if row["sku"]}),
        "valid_rows": len(rows) - error_rows,
        "error_rows": error_rows,
        "warehouse_codes": warehouse_codes,
        "rows": rows,
    }


@api_router.post("/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_product_excel_import(
    payload: ImportConfirmRequest,
    session: AsyncSession = Depends(get_db),
):
    groups = _validate_confirm_rows(payload.rows)
    skus = set(groups)

    existing_result = await session.execute(select(Product.sku).where(Product.sku.in_(skus)))
    existing_skus = set(existing_result.scalars().all())
    if existing_skus:
        raise HTTPException(
            status_code=409,
            detail="این کدهای کالا پس از پیش‌نمایش در سامانه ثبت شده‌اند؛ فایل را دوباره پیش‌نمایش کنید: "
            + "، ".join(sorted(existing_skus)),
        )

    warehouse_ids = {row.warehouse_id for row in payload.rows if row.warehouse_id is not None}
    active_warehouses: dict[UUID, Warehouse] = {}
    if warehouse_ids:
        warehouse_result = await session.execute(
            select(Warehouse).where(Warehouse.id.in_(warehouse_ids), Warehouse.is_active.is_(True))
        )
        active_warehouses = {warehouse.id: warehouse for warehouse in warehouse_result.scalars().all()}
        missing_ids = warehouse_ids - set(active_warehouses)
        if missing_ids:
            raise HTTPException(status_code=422, detail="یکی از انبارهای انتخاب‌شده دیگر فعال یا موجود نیست؛ دوباره انبارها را دریافت کنید")

    for row in payload.rows:
        if row.opening_quantity is not None and row.warehouse_id not in active_warehouses:
            raise HTTPException(status_code=422, detail=f"انبار ردیف {row.row_number} معتبر نیست")

    created_products: dict[str, Product] = {}
    stock_rows = 0
    movement_rows = 0
    price_rows = 0

    try:
        for sku, product_rows in groups.items():
            first = product_rows[0]
            product = Product(
                sku=sku,
                name=first.name,
                description=first.description,
                unit=first.unit,
                is_active=True,
            )
            session.add(product)
            created_products[sku] = product

        await session.flush()

        for sku, product_rows in groups.items():
            product = created_products[sku]
            first = product_rows[0]
            if first.price_toman is not None:
                price_notes = next((row.notes for row in product_rows if row.notes), None)
                session.add(ProductPrice(
                    product_id=product.id,
                    amount=first.price_toman,
                    currency_code="IRT",
                    source="EXCEL_IMPORT",
                    notes=price_notes,
                    is_active=True,
                ))
                price_rows += 1

            for row in product_rows:
                if row.opening_quantity is None:
                    continue
                warehouse = active_warehouses[row.warehouse_id]
                quantity = Decimal(row.opening_quantity)
                session.add(InventoryBalance(
                    product_id=product.id,
                    warehouse_id=warehouse.id,
                    on_hand=quantity,
                    reserved=Decimal("0"),
                ))
                stock_rows += 1
                if quantity > 0:
                    session.add(StockMovement(
                        product_id=product.id,
                        warehouse_id=warehouse.id,
                        movement_type="OPENING",
                        quantity_delta=quantity,
                        reserved_delta=Decimal("0"),
                        reason="موجودی اولیه هنگام ورود گروهی Excel",
                        idempotency_key=f"PRODUCT-EXCEL-OPENING:{product.id}:{warehouse.id}",
                        reference_type="PRODUCT_EXCEL_IMPORT",
                        reference_id=str(product.id),
                        notes=row.notes,
                    ))
                    movement_rows += 1

        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="ثبت گروهی با تعارض اطلاعاتی روبه‌رو شد؛ هیچ بخشی از این فایل ثبت نشد. کدهای کالا و انبارها را دوباره بررسی کنید.",
        ) from exc
    except Exception:
        await session.rollback()
        raise

    return {
        "status": "success",
        "imported_products": len(created_products),
        "price_records": price_rows,
        "inventory_records": stock_rows,
        "stock_movements": movement_rows,
        "message": "ثبت گروهی با موفقیت انجام شد.",
    }
