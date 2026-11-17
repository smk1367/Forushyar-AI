from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_page_is_served():
    with TestClient(app) as client:
        response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "فروش‌یار" in response.text
    assert 'dir="rtl"' in response.text
    assert "/api/v1/auth/login" in response.text
    assert "/api/v1/dashboard/overview" in response.text
    assert 'id="navCustomers"' in response.text
    assert "/api/v1/dashboard/customers" in response.text
    assert "مدیریت مشتریان" in response.text


def test_dashboard_static_asset_exists():
    dashboard_file = Path(__file__).parents[1] / "app" / "static" / "dashboard.html"
    assert dashboard_file.is_file()
    assert dashboard_file.stat().st_size > 5000
