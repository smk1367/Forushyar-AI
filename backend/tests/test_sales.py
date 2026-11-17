from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.sales import _line_stock_status
from app.schemas import QuotePreviewRequest


def test_quote_preview_requires_at_least_one_line():
    with pytest.raises(ValidationError):
        QuotePreviewRequest(lines=[])


def test_quote_preview_rejects_zero_or_negative_quantity():
    with pytest.raises(ValidationError):
        QuotePreviewRequest(lines=[{"sku": "SKU-1", "quantity": "0"}])
    with pytest.raises(ValidationError):
        QuotePreviewRequest(lines=[{"sku": "SKU-1", "quantity": "-1"}])


def test_quote_preview_normalizes_sku_whitespace():
    payload = QuotePreviewRequest(
        lines=[{"sku": "  SKU-1  ", "quantity": "1"}]
    )
    assert payload.lines[0].sku == "SKU-1"


def test_quote_preview_rejects_blank_sku_after_trimming():
    with pytest.raises(ValidationError, match="کد کالا نباید خالی باشد"):
        QuotePreviewRequest(lines=[{"sku": "   ", "quantity": "1"}])


def test_quote_preview_rejects_duplicate_skus_after_normalization():
    with pytest.raises(ValidationError, match="کدهای تکراری"):
        QuotePreviewRequest(
            lines=[
                {"sku": "SKU-1", "quantity": "1"},
                {"sku": " SKU-1 ", "quantity": "2"},
            ]
        )


def test_stock_status_does_not_treat_missing_balance_as_zero():
    assert _line_stock_status(Decimal("2"), [None]) == "UNKNOWN"


def test_stock_status_detects_sufficient_stock_in_any_selected_warehouse():
    assert _line_stock_status(
        Decimal("5"), [Decimal("2"), Decimal("5")]
    ) == "AVAILABLE"


def test_stock_status_reports_insufficient_when_all_known_balances_are_low():
    assert _line_stock_status(
        Decimal("5"), [Decimal("2"), Decimal("3")]
    ) == "INSUFFICIENT"


def test_stock_status_keeps_unknown_when_some_warehouses_have_no_balance():
    assert _line_stock_status(
        Decimal("5"), [Decimal("2"), None]
    ) == "UNKNOWN"
