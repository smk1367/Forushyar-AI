from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.sales_schemas import SalesLineInput, SalesOrderCreate, SalesQuoteCreate
from app.services.sales import calculate_totals


def test_document_totals_use_decimal_rounding_and_apply_tax_after_discounts():
    subtotal, discount, tax, total = calculate_totals(
        [
            (Decimal("2.000"), Decimal("100.00"), Decimal("10.00")),
            (Decimal("1.000"), Decimal("50.00"), Decimal("0.00")),
        ],
        Decimal("15.00"),
        Decimal("9.00"),
    )
    assert subtotal == Decimal("250.00")
    assert discount == Decimal("25.00")
    assert tax == Decimal("20.25")
    assert total == Decimal("245.25")


def test_document_totals_reject_header_discount_above_remaining_subtotal():
    with pytest.raises(ValueError):
        calculate_totals(
            [(Decimal("1.000"), Decimal("100.00"), Decimal("20.00"))],
            Decimal("80.01"),
            Decimal("0"),
        )


@pytest.mark.parametrize("document_model", [SalesQuoteCreate, SalesOrderCreate])
def test_sales_payload_normalizes_currency_and_rejects_duplicate_products(document_model):
    product_id = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(ValidationError):
        document_model(
            customer_id="00000000-0000-0000-0000-000000000010",
            warehouse_id="00000000-0000-0000-0000-000000000020",
            currency_code="irt",
            idempotency_key="document-request-0001",
            lines=[
                {"product_id": product_id, "quantity": "1"},
                {"product_id": product_id, "quantity": "2"},
            ],
        )
    valid = document_model(
        customer_id="00000000-0000-0000-0000-000000000010",
        warehouse_id="00000000-0000-0000-0000-000000000020",
        currency_code="irt",
        idempotency_key="document-request-0002",
        lines=[{"product_id": product_id, "quantity": "1"}],
    )
    assert valid.currency_code == "IRT"


def test_sales_line_requires_positive_quantity_and_cent_precision_price():
    with pytest.raises(ValidationError):
        SalesLineInput(
            product_id="00000000-0000-0000-0000-000000000001",
            quantity="0",
            unit_price="10.00",
        )
    with pytest.raises(ValidationError):
        SalesLineInput(
            product_id="00000000-0000-0000-0000-000000000001",
            quantity="1",
            unit_price="10.001",
        )
