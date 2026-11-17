from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from app.main import app


def get_route_paths():
    return [route.path for route in iter_route_contexts(app.routes)]


def test_liveness_endpoint():
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_inventory_operation_routes_are_registered():
    paths = set(get_route_paths())

    assert "/api/v1/inventory/receipts" in paths
    assert "/api/v1/inventory/issues" in paths
    assert "/api/v1/inventory/transfers" in paths
    assert "/api/v1/inventory/reservations" in paths
    assert "/api/v1/inventory/{sku}/movements" in paths


def test_static_inventory_paths_are_registered_before_sku_catch_all():
    paths = get_route_paths()

    assert paths.index("/api/v1/inventory/transfers") < paths.index(
        "/api/v1/inventory/{sku}"
    )
    assert paths.index("/api/v1/inventory/reservations") < paths.index(
        "/api/v1/inventory/{sku}"
    )
