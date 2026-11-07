import pytest
from pydantic import ValidationError

from app.schemas import InventoryIssue, InventoryReceipt, StockTransferCreate


def test_receipt_requires_positive_quantity():
    with pytest.raises(ValidationError):
        InventoryReceipt(
            product_id="00000000-0000-0000-0000-000000000001",
            warehouse_id="00000000-0000-0000-0000-000000000002",
            quantity="0",
            idempotency_key="receipt-key-0001",
        )


def test_issue_requires_positive_quantity():
    with pytest.raises(ValidationError):
        InventoryIssue(
            product_id="00000000-0000-0000-0000-000000000001",
            warehouse_id="00000000-0000-0000-0000-000000000002",
            quantity="-1",
            idempotency_key="issue-key-00001",
        )


def test_transfer_requires_positive_quantity():
    with pytest.raises(ValidationError):
        StockTransferCreate(
            product_id="00000000-0000-0000-0000-000000000001",
            from_warehouse_id="00000000-0000-0000-0000-000000000002",
            to_warehouse_id="00000000-0000-0000-0000-000000000003",
            quantity="0",
            idempotency_key="transfer-key-01",
        )
