from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import current_employee
from app.db.session import get_db
from app.main import app
from app.security import require_csrf


class _Session:
    async def scalars(self, _statement):
        raise AssertionError("Unauthorized role must be rejected before database access")


def _request_as(role, method, path, **kwargs):
    employee = SimpleNamespace(role=role)

    async def override_employee():
        return employee

    async def override_db():
        yield _Session()

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


def test_dashboard_order_list_rejects_warehouse_role():
    response = _request_as("WAREHOUSE", "GET", "/api/v1/dashboard/orders")
    assert response.status_code == 403


def test_dashboard_order_options_rejects_warehouse_role():
    response = _request_as("WAREHOUSE", "GET", "/api/v1/dashboard/order-options")
    assert response.status_code == 403


def test_dashboard_order_creation_rejects_warehouse_role():
    response = _request_as(
        "WAREHOUSE",
        "POST",
        "/api/v1/dashboard/orders",
        headers={"Idempotency-Key": "order-test-123"},
        json={"lines": [{"sku": "SKU-001", "quantity": "1"}]},
    )
    assert response.status_code == 403


def test_dashboard_order_cancellation_rejects_warehouse_role():
    response = _request_as(
        "WAREHOUSE",
        "POST",
        f"/api/v1/dashboard/orders/{uuid4()}/cancel",
        headers={"Idempotency-Key": "cancel-test-123"},
        json={"reason": "درخواست آزمایشی لغو"},
    )
    assert response.status_code == 403
