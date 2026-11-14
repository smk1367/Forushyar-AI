from __future__ import annotations

import unicodedata
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
MAX_IMPORT_COLUMNS = 32
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024

TEMPLATE_HEADERS = ["کد کالا", "نام کالا", "توضیحات", "واحد", "قیمت (تومان)", "کد انبار", "موجودی اولیه", "یادداشت"]
ALIASES = {
    "sku": "sku", "کدکالا": "sku", "کد": "sku",
    "name": "name", "نامکالا": "name",
    "description": "description", "توضیحات": "description",
    "unit": "unit", "واحد": "unit",
    "قیمتتومان": "price_toman", "قیمت(تومان)": "price_toman", "pricetoman": "price_toman",
    "کدانبار": "warehouse_code", "warehousecode": "warehouse_code",
    "موجودیاولیه": "opening_quantity", "openingquantity": "opening_quantity",
    "یادداشت": "notes", "notes": "notes",
}


def _header(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().lower().replace(" ", "").replace("\u200c", "")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    result = str(value).strip()
    return result or None


def _decimal(value: Any, label: str, places: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} باید عددی باشد")
    value = str(value).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    value = value.replace("٬", "").replace(",", "").replace("،", "").replace("٫", ".").replace(" ", "")
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise ValueError
        quantized = number.quantize(Decimal(1).scaleb(-places))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} باید عددی معتبر و غیرمنفی باشد") from exc
    if quantized != number or len((format(quantized, "f").split(".")[0].lstrip("0") or "0") + format(quantized, "f").split(".")[-1]) > 18:
        raise ValueError(f"{label} حداکثر {places} رقم اعشار و ۱۸ رقم دارد")
    return format(quantized, "f")


def parse_product_workbook(content: bytes) -> list[dict[str, Any]]:
    if not content:
        raise ValueError("فایل خالی است")
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("حجم فایل نباید بیشتر از ۵ مگابایت باشد")
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 1000 or sum(x.file_size for x in members) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("ساختار یا حجم بازشده فایل Excel بیش از حد مجاز است")
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names or archive.testzip():
                raise ValueError("فایل XLSX معتبر نیست")
    except BadZipFile as exc:
        raise ValueError("فایل XLSX معتبر نیست") from exc

    try:
        book = load_workbook(BytesIO(content), read_only=True, data_only=False)
    except Exception as exc:
        raise ValueError("فایل Excel قابل خواندن نیست") from exc
    try:
        if not book.worksheets:
            raise ValueError("فایل هیچ برگه‌ای ندارد")
        sheet = book.worksheets[0]
        if sheet.max_column > MAX_IMPORT_COLUMNS or sheet.max_row > MAX_IMPORT_ROWS + 1:
            raise ValueError("تعداد ردیف یا ستون فایل بیش از حد مجاز است")
        iterator = sheet.iter_rows(values_only=True)
        headers = next(iterator, None)
        if not headers:
            raise ValueError("برگه Excel خالی است")
        columns: dict[str, int] = {}
        for i, raw in enumerate(headers):
            field = ALIASES.get(_header(raw))
            if field:
                if field in columns:
                    raise ValueError(f"ستون تکراری: {raw}")
                columns[field] = i
        missing = [label for label, key in (("کد کالا", "sku"), ("نام کالا", "name")) if key not in columns]
        if missing:
            raise ValueError("ستون‌های الزامی پیدا نشدند: " + "، ".join(missing))
        rows = []
        for row_no, values in enumerate(iterator, 2):
            if row_no > MAX_IMPORT_ROWS + 1:
                raise ValueError("حداکثر ۵۰۰۰ ردیف مجاز است")
            if not any(v is not None and str(v).strip() for v in values):
                continue
            def cell(key: str):
                i = columns.get(key)
                return values[i] if i is not None and i < len(values) else None
            errors = []
            if any(isinstance(v, str) and v.startswith("=") for v in values):
                errors.append("فرمول Excel مجاز نیست")
            sku, name = _text(cell("sku")), _text(cell("name"))
            unit, warehouse = _text(cell("unit")) or "عدد", _text(cell("warehouse_code"))
            if not sku: errors.append("کد کالا الزامی است")
            if sku and len(sku) > 80: errors.append("کد کالا بیش از ۸۰ کاراکتر است")
            if not name: errors.append("نام کالا الزامی است")
            if name and len(name) > 240: errors.append("نام کالا بیش از ۲۴۰ کاراکتر است")
            if len(unit) > 24: errors.append("واحد بیش از ۲۴ کاراکتر است")
            try: price = _decimal(cell("price_toman"), "قیمت", 2)
            except ValueError as exc: price = None; errors.append(str(exc))
            try: qty = _decimal(cell("opening_quantity"), "موجودی", 3)
            except ValueError as exc: qty = None; errors.append(str(exc))
            rows.append({
                "row_number": row_no, "sku": sku, "name": name,
                "description": _text(cell("description")), "unit": unit,
                "price_toman": price, "warehouse_code": warehouse,
                "opening_quantity": qty, "notes": _text(cell("notes")), "errors": errors,
            })
        if not rows:
            raise ValueError("هیچ ردیف کالایی پیدا نشد")
        return rows
    finally:
        book.close()


def validate_product_import_rows(rows, existing_skus: set[str], warehouse_status: dict[str, bool]):
    normalized = [{**r, "errors": list(r.get("errors", []))} for r in rows]
    groups: dict[str, list[dict]] = {}
    for row in normalized:
        if row.get("sku"):
            groups.setdefault(row["sku"].casefold(), []).append(row)
    existing = {s.casefold() for s in existing_skus}
    warehouses = {k.casefold(): v for k, v in warehouse_status.items()}
    for key, group in groups.items():
        first = group[0]
        if key in existing:
            for row in group: row["errors"].append("این کد کالا قبلاً ثبت شده است")
        if len(group) > 1:
            for row in group:
                if not row.get("warehouse_code") or row.get("opening_quantity") is None:
                    row["errors"].append("برای SKU تکراری، هر ردیف باید انبار و موجودی مستقل داشته باشد")
        for row in group:
            for field, label in (("name", "نام"), ("description", "توضیحات"), ("unit", "واحد"), ("price_toman", "قیمت")):
                if row.get(field) != first.get(field):
                    row["errors"].append(f"{label} ردیف‌های یک کالا باید یکسان باشد")
            code, qty = row.get("warehouse_code"), row.get("opening_quantity")
            if bool(code) != (qty is not None):
                row["errors"].append("برای موجودی، کد انبار و تعداد باید با هم وارد شوند")
            if code:
                key_wh = code.casefold()
                if key_wh not in warehouses: row["errors"].append("کد انبار در سامانه پیدا نشد")
                elif not warehouses[key_wh]: row["errors"].append("انبار غیرفعال است")
        seen = set()
        for row in group:
            code = row.get("warehouse_code")
            if code and code.casefold() in seen:
                row["errors"].append("انبار برای این کالا تکراری است")
            if code: seen.add(code.casefold())
    for row in normalized: row["errors"] = list(dict.fromkeys(row["errors"]))
    valid = sum(not r["errors"] for r in normalized)
    return {
        "valid": valid == len(normalized), "rows": normalized,
        "summary": {"rows_total": len(normalized), "rows_valid": valid, "rows_invalid": len(normalized)-valid,
                    "products_total": len(groups), "opening_stock_rows": sum(r.get("opening_quantity") is not None for r in normalized)}
    }


def build_product_import_template() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "کالاها"
    sheet.sheet_view.rightToLeft = True
    sheet.append(TEMPLATE_HEADERS)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:H1"
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(name="Tahoma", bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center")
    for i, width in enumerate([20,30,36,14,20,22,20,32], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    guide = book.create_sheet("راهنما")
    guide.sheet_view.rightToLeft = True
    for row in [
        ["راهنمای ورود گروهی کالا"], ["هر ردیف یک کالا است."],
        ["برای ثبت یک کالا در چند انبار، SKU را در چند ردیف تکرار کنید و در هر ردیف انبار و موجودی را وارد کنید."],
        ["نام، توضیحات، واحد و قیمت ردیف‌های تکراری باید یکسان باشد."],
        ["برای کالایی که شمارش نشده، انبار و موجودی را خالی بگذارید."],
        ["قیمت بر حسب تومان است؛ خالی یعنی ثبت قیمت انجام نشود و صفر یعنی قیمت صفر."],
        ["فقط XLSX، حداکثر ۵ مگابایت و ۵۰۰۰ ردیف داده پذیرفته می‌شود."],
    ]: guide.append(row)
    guide.column_dimensions["A"].width = 110
    for row in guide.iter_rows():
        for cell in row: cell.alignment = Alignment(horizontal="right", wrap_text=True)
    out = BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()
