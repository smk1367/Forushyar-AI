from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import current_employee
from app.db.session import get_db
from app.main import app
from app.security import require_csrf


class _Rows:
    def __init__(self, rows=None):
        self.rows = rows or []

    def all(self):
        return self.rows


class _Session:
    def __init__(self):
        self.added = []

    async def scalars(self, _statement):
        return _Rows()

    async def execute(self, _statement):
        return _Rows()

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, _item):
        return None


def _request_as(role, method, path, *, session=None, **kwargs):
    employee = SimpleNamespace(role=role)
    db = session or _Session()

    async def override_employee():
        return employee

    async def override_db():
        yield db

    app.dependency_overrides[current_employee] = override_employee
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_csrf] = lambda: None
    try:
        with TestClient(app) as client:
            return client.request(method, path, **kwargs)
    finally:
        app.dependency_overrides.pop(current_employee, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_csrf, None)


def test_dashboard_inventory_routes_are_registered():
    paths = {route.path for route in app.routes if getattr(route, "path", None)}
    assert "/api/v1/dashboard/products" in paths
    assert "/api/v1/dashboard/warehouses" in paths
    assert "/api/v1/dashboard/inventory" in paths
    assert "/api/v1/dashboard/inventory/movements" in paths
    assert "/api/v1/dashboard/inventory/receipts" in paths
    assert "/api/v1/dashboard/inventory/issues" in paths
    assert "/api/v1/dashboard/inventory/adjustments" in paths
    assert "/api/v1/dashboard/inventory/transfers" in paths


def test_sales_role_can_read_products_but_cannot_create_them():
    read_response = _request_as("SALES", "GET", "/api/v1/dashboard/products")
    assert read_response.status_code == 200
    assert read_response.json() == []

    write_response = _request_as(
        "SALES",
        "POST",
        "/api/v1/dashboard/products",
        json={"sku": "SKU-001", "name": "کالای آزمایشی", "unit": "عدد"},
    )
    assert write_response.status_code == 403


def test_warehouse_role_cannot_create_or_manage_warehouses():
    response = _request_as(
        "WAREHOUSE",
        "POST",
        "/api/v1/dashboard/warehouses",
        json={"code": "WH-02", "name": "انبار دوم"},
    )
    assert response.status_code == 403


def test_sales_role_cannot_post_inventory_movements():
    response = _request_as(
        "SALES",
        "POST",
        "/api/v1/dashboard/inventory/receipts",
        json={
            "product_id": str(uuid4()),
            "warehouse_id": str(uuid4()),
            "quantity": "2",
            "reason": "رسید آزمایشی",
            "idempotency_key": "dashboard-test-receipt-001",
        },
    )
    assert response.status_code == 403


def test_warehouse_role_can_read_inventory():
    response = _request_as("WAREHOUSE", "GET", "/api/v1/dashboard/inventory")
    assert response.status_code == 200
    assert response.json() == []
