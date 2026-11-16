from io import BytesIO

from openpyxl import Workbook

from app.api.product_import import parse_workbook_bytes


def make_workbook(rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "کالاها"
    worksheet.append([
        "کد کالا", "نام کالا", "توضیحات", "واحد", "قیمت (تومان)",
        "کد انبار", "موجودی اولیه", "یادداشت",
    ])
    for row in rows:
        worksheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def test_import_parser_reads_sample_style_data_and_persian_digits():
    data = make_workbook([
        ["۵۰۰", "رابیتس13", "مشخصات", 400, "۱۹۵٬۰۰۰", "10001", "۲۰۰۰", "یادداشت"],
    ])
    rows = parse_workbook_bytes(data)
    assert len(rows) == 1
    assert rows[0]["sku"] == "500"  # Persian digits in identifiers are normalized to ASCII.
    assert rows[0]["price_toman"] == "195000"
    assert rows[0]["opening_quantity"] == "2000"
    assert rows[0]["errors"] == []


def test_same_product_can_have_stock_rows_for_different_warehouses():
    data = make_workbook([
        ["500", "رابیتس", "توضیح", "400 گرم", 195000, "10001", 20, None],
        ["500", "رابیتس", "توضیح", "400 گرم", 195000, "10002", 30, None],
    ])
    rows = parse_workbook_bytes(data)
    assert len(rows) == 2
    assert all(not row["errors"] for row in rows)


def test_duplicate_sku_for_same_warehouse_is_rejected_in_preview():
    data = make_workbook([
        ["500", "رابیتس", None, "عدد", 195000, "10001", 20, None],
        ["500", "رابیتس", None, "عدد", 195000, "10001", 30, None],
    ])
    rows = parse_workbook_bytes(data)
    assert any("بیش از یک‌بار" in error for row in rows for error in row["errors"])


def test_stock_requires_warehouse_code_and_quantity_together():
    data = make_workbook([["500", "رابیتس", None, "عدد", None, "10001", None, None]])
    rows = parse_workbook_bytes(data)
    assert any("کد انبار و موجودی اولیه" in error for error in rows[0]["errors"])
