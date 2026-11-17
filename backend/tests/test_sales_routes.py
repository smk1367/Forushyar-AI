from fastapi.routing import iter_route_contexts

from app.main import app


def test_sales_quote_preview_route_is_registered():
    paths = [route.path for route in iter_route_contexts(app.routes)]
    assert "/api/v1/sales/quote-preview" in paths
