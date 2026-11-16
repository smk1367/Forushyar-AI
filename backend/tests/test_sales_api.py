from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.customer import Customer
from app.models.warehouse import Warehouse

TEST_API_KEY = "forushyar-ci-test-key-0000000000000000"


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeSession:
    def __init__(self, scalar_results, get_results=None):
        self._scalar_results = list(scalar_results)
        self._get_results = get_results or {}

    async def scalars(self, _statement):
        if not self._scalar_results:
            raise AssertionError("Unexpected database query in sales API test")
        return _ScalarResult(self._scalar_results.pop(0))

    async def get(self, model, identity):
        return self._get_results.get((model, identity))


def _records(*, on_hand="2000.000", reserved="0.000", currency="IRT"):
    product_id = uuid4()
    warehouse_id = uuid4()
    product = SimpleNamespace(
        id=product_id,
        sku="500",
        name="رابیتس13",
        unit="400",
        is_active=True,
    )
    warehouse = SimpleNamespace(
        id=warehouse_id,
        code="WH-TEH-01",
        name="تهران - آهن مکان",
        is_active=True,
    )
    price = SimpleNamespace(
        product_id=product_id,
        amount=Decimal("195000.00"),
        currency_code=currency,
    )
    balance = SimpleNamespace(
        product_id=product_id,
        warehouse_id=warehouse_id,
        on_hand=Decimal(on_hand),
        reserved=Decimal(reserved),
    )
    return product, warehouse, price, balance


def _post_quote(monkeypatch, fake_session, payload, *, api_key=TEST_API_KEY):
    monkeypatch.setattr(settings, "api_key", TEST_API_KEY)

    async def override_get_db():
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    headers = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key

    try:
        with TestClient(app) as client:
            return client.post(
                "/api/v1/sales/quote-preview",
                json=payload,
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_quote_preview_api_returns_exact_decimal_totals_and_recorded_stock(monkeypatch):
    product, warehouse, price, balance = _records()
    session = _FakeSession(
        [[product], [price], [balance]],
        {(Warehouse, warehouse.id): warehouse},
    )

    response = _post_quote(
        monkeypatch,
        session,
        {
            "warehouse_id": str(warehouse.id),
            "lines": [{"sku": "500", "quantity": "2"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["currency_code"] == "IRT"
    assert body["subtotal"] == "390000.00"
    assert body["line_count"] == 1
    assert body["fulfillment_status"] == "AVAILABLE"
    assert body["lines"][0]["line_total"] == "390000.00"
    assert body["lines"][0]["stock_status"] == "AVAILABLE"
    assert body["lines"][0]["availability"][0]["available"] == "2000.000"


def test_quote_preview_api_returns_unknown_for_missing_balance_record(monkeypatch):
    product, warehouse, price, _balance = _records()
    session = _FakeSession(
        [[product], [price], []],
        {(Warehouse, warehouse.id): warehouse},
    )

    response = _post_quote(
        monkeypatch,
        session,
        {
            "warehouse_id": str(warehouse.id),
            "lines": [{"sku": "500", "quantity": "2"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fulfillment_status"] == "UNKNOWN"
    assert body["lines"][0]["stock_status"] == "UNKNOWN"
    assert body["lines"][0]["availability"][0]["available"] is None


def test_quote_preview_api_returns_insufficient_for_low_recorded_stock(monkeypatch):
    product, warehouse, price, balance = _records(on_hand="1.000")
    session = _FakeSession(
        [[product], [price], [balance]],
        {(Warehouse, warehouse.id): warehouse},
    )

    response = _post_quote(
        monkeypatch,
        session,
        {
            "warehouse_id": str(warehouse.id),
            "lines": [{"sku": "500", "quantity": "2"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["fulfillment_status"] == "INSUFFICIENT"
    assert response.json()["lines"][0]["stock_status"] == "INSUFFICIENT"


def test_quote_preview_api_rejects_missing_current_price(monkeypatch):
    product, warehouse, _price, _balance = _records()
    session = _FakeSession([[product], []], {(Warehouse, warehouse.id): warehouse})

    response = _post_quote(
        monkeypatch,
        session,
        {
            "warehouse_id": str(warehouse.id),
            "lines": [{"sku": "500", "quantity": "2"}],
        },
    )

    assert response.status_code == 409
    assert "قیمت معتبر و جاری" in response.json()["detail"]


def test_quote_preview_api_rejects_unknown_sku(monkeypatch):
    session = _FakeSession([[]])

    response = _post_quote(
        monkeypatch,
        session,
        {
            "lines": [{"sku": "NOT-FOUND", "quantity": "1"}],
        },
    )

    assert response.status_code == 404
    assert "کالای فعال" in response.json()["detail"]


def test_quote_preview_api_rejects_mixed_currencies(monkeypatch):
    product_a, _warehouse_a, price_a, _balance_a = _records(currency="IRT")
    product_b = SimpleNamespace(
        id=uuid4(), sku="501", name="کالای دوم", unit="عدد", is_active=True
    )
    price_b = SimpleNamespace(
        product_id=product_b.id,
        amount=Decimal("12.00"),
        currency_code="IRR",
    )
    session = _FakeSession([[product_a, product_b], [price_a, price_b]])

    response = _post_quote(
        monkeypatch,
        session,
        {"lines": [
            {"sku": "500", "quantity": "1"},
            {"sku": "501", "quantity": "1"},
        ]},
    )

    assert response.status_code == 409
    assert "واحد پول" in response.json()["detail"]


def test_quote_preview_api_rejects_unknown_customer(monkeypatch):
    customer_id = uuid4()
    session = _FakeSession(
        [],
        {(Customer, customer_id): None},
    )

    response = _post_quote(
        monkeypatch,
        session,
        {
            "customer_id": str(customer_id),
            "lines": [{"sku": "500", "quantity": "1"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "مشتری یافت نشد"


def test_quote_preview_api_rejects_inactive_selected_warehouse(monkeypatch):
    product, warehouse, price, _balance = _records()
    inactive_warehouse = SimpleNamespace(
        id=warehouse.id,
        code=warehouse.code,
        name=warehouse.name,
        is_active=False,
    )
    session = _FakeSession(
        [[product], [price]],
        {(Warehouse, warehouse.id): inactive_warehouse},
    )

    response = _post_quote(
        monkeypatch,
        session,
        {
            "warehouse_id": str(warehouse.id),
            "lines": [{"sku": "500", "quantity": "1"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "انبار فعال یافت نشد"


def test_quote_preview_api_with_no_active_warehouses_returns_unknown(monkeypatch):
    product, _warehouse, price, _balance = _records()
    session = _FakeSession([[product], [price], []])

    response = _post_quote(
        monkeypatch,
        session,
        {"lines": [{"sku": "500", "quantity": "1"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fulfillment_status"] == "UNKNOWN"
    assert body["lines"][0]["stock_status"] == "UNKNOWN"
    assert body["lines"][0]["availability"] == []


def test_quote_preview_api_requires_api_key(monkeypatch):
    session = _FakeSession([])

    response = _post_quote(
        monkeypatch,
        session,
        {"lines": [{"sku": "500", "quantity": "1"}]},
        api_key=None,
    )

    assert response.status_code == 401
