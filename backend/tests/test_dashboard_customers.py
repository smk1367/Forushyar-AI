from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import current_employee
from app.db.session import get_db
from app.main import app
from app.models.customer import Customer
from app.security import require_csrf


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.added = []

    async def scalars(self, _statement):
        return _Rows(self.rows)

    def add(self, item):
        if isinstance(item, Customer) and item.id is None:
            item.id = uuid4()
        self.added.append(item)

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, item):
        if isinstance(item, Customer) and item.created_at is None:
            item.created_at = datetime.now(timezone.utc)


def _request_as(role, session, method, path, **kwargs):
    employee = SimpleNamespace(role=role)

    async def override_employee():
        return employee

    async def override_db():
        yield session

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


def test_dashboard_customer_list_is_available_to_sales_role():
    customer = SimpleNamespace(
        id=uuid4(),
        full_name="مشتری نمونه",
        phone="09120000000",
        email="customer@example.com",
        created_at=datetime.now(timezone.utc),
    )
    response = _request_as("SALES", _Session([customer]), "GET", "/api/v1/dashboard/customers")

    assert response.status_code == 200
    assert response.json()[0]["full_name"] == "مشتری نمونه"


def test_dashboard_customer_list_rejects_warehouse_role():
    response = _request_as("WAREHOUSE", _Session(), "GET", "/api/v1/dashboard/customers")

    assert response.status_code == 403


def test_dashboard_customer_creation_persists_customer_for_sales_role():
    session = _Session()
    response = _request_as(
        "SALES",
        session,
        "POST",
        "/api/v1/dashboard/customers",
        json={
            "full_name": "مشتری تازه",
            "phone": "09121112233",
            "email": "new@example.com",
        },
    )

    assert response.status_code == 201
    assert response.json()["full_name"] == "مشتری تازه"
    assert len(session.added) == 1
    assert session.added[0].phone == "09121112233"


def test_dashboard_customer_creation_rejects_warehouse_role():
    session = _Session()
    response = _request_as(
        "WAREHOUSE",
        session,
        "POST",
        "/api/v1/dashboard/customers",
        json={"full_name": "مشتری تازه"},
    )

    assert response.status_code == 403
    assert session.added == []
