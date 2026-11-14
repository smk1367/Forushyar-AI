from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_liveness_endpoint():
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_inventory_operation_routes_are_registered():
    paths = set(app.openapi()["paths"])

    assert "/api/v1/inventory/receipts" in paths
    assert "/api/v1/inventory/issues" in paths
    assert "/api/v1/inventory/transfers" in paths
    assert "/api/v1/inventory/reservations" in paths
    assert "/api/v1/inventory/{sku}/movements" in paths


def test_static_inventory_paths_are_registered_before_sku_catch_all():
    headers = {"X-API-Key": settings.api_key}

    with TestClient(app) as client:
        for path in (
            "/api/v1/inventory/transfers",
            "/api/v1/inventory/reservations",
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, (
                f"{path}: HTTP {response.status_code} - {response.text}"
            )
