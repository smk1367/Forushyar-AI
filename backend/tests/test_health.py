from fastapi.testclient import TestClient

from app.main import app


def test_liveness_endpoint():
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_inventory_operation_routes_are_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/inventory/receipts" in paths
    assert "/api/v1/inventory/issues" in paths
    assert "/api/v1/inventory/transfers" in paths
    assert "/api/v1/inventory/reservations" in paths
    assert "/api/v1/inventory/{sku}/movements" in paths


def test_static_inventory_paths_are_registered_before_sku_catch_all():
    paths = [route.path for route in app.routes if getattr(route, "path", "")]
    assert paths.index("/api/v1/inventory/transfers") < paths.index("/api/v1/inventory/{sku}")
    assert paths.index("/api/v1/inventory/reservations") < paths.index("/api/v1/inventory/{sku}")
