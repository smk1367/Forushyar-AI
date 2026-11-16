from pydantic import ValidationError
import pytest

from app.schemas import SalesOrderCreate, SalesOrderCancelRequest


def test_sales_order_requires_at_least_one_line():
    with pytest.raises(ValidationError):
        SalesOrderCreate(lines=[])


def test_sales_order_rejects_zero_or_negative_quantity():
    with pytest.raises(ValidationError):
        SalesOrderCreate(lines=[{"sku": "SKU-1", "quantity": "0"}])
    with pytest.raises(ValidationError):
        SalesOrderCreate(lines=[{"sku": "SKU-1", "quantity": "-1"}])


def test_sales_order_normalizes_sku_and_rejects_duplicates_after_trim():
    with pytest.raises(ValidationError, match="کدهای تکراری"):
        SalesOrderCreate(
            lines=[
                {"sku": "SKU-1", "quantity": "1"},
                {"sku": " SKU-1 ", "quantity": "2"},
            ]
        )


def test_sales_order_accepts_explicit_warehouse_per_line():
    import uuid

    warehouse_id = uuid.uuid4()
    payload = SalesOrderCreate(
        lines=[{"sku": "SKU-1", "quantity": "1.250", "warehouse_id": str(warehouse_id)}]
    )
    assert payload.lines[0].warehouse_id == warehouse_id


def test_cancel_reason_is_normalized_and_must_not_be_blank():
    payload = SalesOrderCancelRequest(reason="  عدم   تأیید مشتری  ")
    assert payload.reason == "عدم تأیید مشتری"

    with pytest.raises(ValidationError):
        SalesOrderCancelRequest(reason="   ")
