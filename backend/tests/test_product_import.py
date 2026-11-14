from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.product_import import (
    build_product_import_template,
    parse_product_workbook,
    validate_product_import_rows,
)


def workbook_bytes(rows):
    book = Workbook()
    sheet = book.active
    sheet.append(["کد کالا", "نام کالا", "قیمت (تومان)", "کد انبار", "موجودی اولیه"])
    for row in rows:
        sheet.append(row)
    out = BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()


def test_template_is_parseable():
    content = build_product_import_template()
    assert content[:2] == b"PK"
    assert len(content) > 1000


def test_persian_digits_and_optional_stock():
    rows = parse_product_workbook(workbook_bytes([["A-1", "میلگرد", "۱۲۵٬۰۰۰", None, None]]))
    assert rows[0]["price_toman"] == "125000.00"
    assert rows[0]["opening_quantity"] is None
    assert rows[0]["errors"] == []


def test_duplicate_sku_across_two_warehouses_is_one_product_group():
    rows = parse_product_workbook(workbook_bytes([
        ["A-1", "میلگرد", "125000", "WH1", "10"],
        ["A-1", "میلگرد", "125000", "WH2", "0"],
    ]))
    result = validate_product_import_rows(rows, set(), {"WH1": True, "WH2": True})
    assert result["valid"] is True
    assert result["summary"]["products_total"] == 1
    assert result["summary"]["opening_stock_rows"] == 2


def test_existing_sku_and_unknown_warehouse_are_rejected():
    rows = parse_product_workbook(workbook_bytes([["A-1", "میلگرد", "125000", "UNKNOWN", "2"]]))
    result = validate_product_import_rows(rows, {"a-1"}, {})
    assert result["valid"] is False
    assert any("قبلاً ثبت" in e for e in result["rows"][0]["errors"])
    assert any("پیدا نشد" in e for e in result["rows"][0]["errors"])


def test_duplicate_warehouse_for_same_product_is_rejected():
    rows = parse_product_workbook(workbook_bytes([
        ["A-1", "میلگرد", "125000", "WH1", "10"],
        ["A-1", "میلگرد", "125000", "WH1", "5"],
    ]))
    result = validate_product_import_rows(rows, set(), {"WH1": True})
    assert not result["valid"]
    assert any("تکراری" in e for row in result["rows"] for e in row["errors"])


def test_invalid_zip_is_rejected():
    with pytest.raises(ValueError):
        parse_product_workbook(b"not-an-xlsx")


def test_missing_required_header_is_rejected():
    book = Workbook()
    book.active.append(["نام کالا"])
    out = BytesIO()
    book.save(out)
    book.close()
    with pytest.raises(ValueError, match="کد کالا"):
        parse_product_workbook(out.getvalue())
