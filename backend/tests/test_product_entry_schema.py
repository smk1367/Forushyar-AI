from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.api.product_entry import ProductEntryCreate


def test_trim_text():
    p = ProductEntryCreate(
        sku=" SKU-1001 ",
        name=" کالای آزمایشی ",
        unit=" عدد "
    )
    assert p.sku == "SKU-1001"
    assert p.name == "کالای آزمایشی"


def test_stock_requires_warehouse():
    with pytest.raises(ValidationError):
        ProductEntryCreate(
            sku="SKU-1002",
            name="کالا",
            opening_quantity="5"
        )


def test_zero_stock_is_valid():
    p = ProductEntryCreate(
        sku="SKU-1003",
        name="کالا",
        warehouse_id=uuid4(),
        opening_quantity="0"
    )
    assert str(p.opening_quantity) == "0"


def test_negative_stock_is_rejected():
    with pytest.raises(ValidationError):
        ProductEntryCreate(
            sku="SKU-1004",
            name="کالا",
            warehouse_id=uuid4(),
            opening_quantity="-1"
        )


def test_negative_price_is_rejected():
    with pytest.raises(ValidationError):
        ProductEntryCreate(
            sku="SKU-1005",
            name="کالا",
            price_toman="-1"
        )
