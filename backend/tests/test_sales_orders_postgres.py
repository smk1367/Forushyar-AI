"""Real PostgreSQL integration tests.

These tests mutate only the dedicated CI database. They are skipped by default
so running pytest on a developer or production server cannot write test rows
to the configured application database.
"""
import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import engine as app_engine
from app.main import app
from app.models.inventory import InventoryBalance, StockMovement, StockReservation
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.sales_order import SalesOrder
from app.models.warehouse import Warehouse
from app.schemas import SalesOrderCreate
from app.services.sales_orders import create_sales_order

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="requires a dedicated disposable PostgreSQL test database",
)

TEST_API_KEY = "forushyar-ci-test-key-0000000000000000"


def _db_run(operation):
    async def runner():
        db_engine = create_async_engine(settings.database_url, poolclass=NullPool)
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        try:
            return await operation(factory)
        finally:
            await db_engine.dispose()

    return asyncio.run(runner())


def _seed_catalog(products, warehouse_codes):
    suffix = uuid.uuid4().hex[:10].upper()
    normalized_products = []
    for index, item in enumerate(products):
        normalized_products.append(
            {
                "sku": f"T{suffix}{index}",
                "name": f"Integration product {index}",
                "price": Decimal(str(item["price"])),
                "stocks": item["stocks"],
            }
        )
    codes = [f"IT-{suffix}-{code}" for code in warehouse_codes]

    async def operation(factory):
        async with factory() as session:
            warehouses = {
                code: Warehouse(code=code, name=f"Integration warehouse {code}", is_active=True)
                for code in codes
            }
            session.add_all(warehouses.values())
            await session.flush()

            products_by_sku = {}
            for item in normalized_products:
                product = Product(
                    sku=item["sku"],
                    name=item["name"],
                    unit="عدد",
                    is_active=True,
                )
                session.add(product)
                await session.flush()
                products_by_sku[item["sku"]] = product
                session.add(
                    ProductPrice(
                        product_id=product.id,
                        amount=item["price"],
                        currency_code="IRT",
                        is_active=True,
                        source="INTEGRATION_TEST",
                    )
                )
                for code, quantity in item["stocks"].items():
                    session.add(
                        InventoryBalance(
                            product_id=product.id,
                            warehouse_id=warehouses[f"IT-{suffix}-{code}"].id,
                            on_hand=Decimal(str(quantity)),
                            reserved=Decimal("0"),
                        )
                    )
            await session.commit()
            return {
                "suffix": suffix,
                "products": {
                    sku: {"id": product.id, "sku": sku}
                    for sku, product in products_by_sku.items()
                },
                "warehouses": {
                    key: {"id": warehouse.id, "code": warehouse.code}
                    for key, warehouse in warehouses.items()
                },
            }

    return _db_run(operation)


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", TEST_API_KEY)
    with TestClient(app) as client:
        yield client


def _headers(key):
    return {"X-API-Key": TEST_API_KEY, "Idempotency-Key": key}


def test_order_snapshot_multiwarehouse_idempotency_and_idempotent_cancel(api_client):
    seeded = _seed_catalog(
        [{"price": "10.00", "stocks": {"A": "2", "B": "3"}}],
        ["A", "B"],
    )
    sku = next(iter(seeded["products"]))
    payload = {"lines": [{"sku": sku, "quantity": "4"}]}
    headers = _headers("order-it-" + seeded["suffix"])

    created = api_client.post("/api/v1/sales/orders", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    order_id = body["id"]
    assert body["status"] == "PENDING_PAYMENT"
    assert body["currency_code"] == "IRT"
    assert body["subtotal"] == "40.00"
    assert body["lines"][0]["unit_price"] == "10.00"
    assert sorted(Decimal(x["quantity"]) for x in body["lines"][0]["allocations"]) == [
        Decimal("2.000"),
        Decimal("2.000"),
    ]
    assert [event["event_type"] for event in body["events"]] == ["CREATED"]

    replay = api_client.post("/api/v1/sales/orders", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["id"] == order_id

    changed_payload = {"lines": [{"sku": sku, "quantity": "1"}]}
    conflict = api_client.post(
        "/api/v1/sales/orders", json=changed_payload, headers=headers
    )
    assert conflict.status_code == 409

    async def change_price(factory):
        async with factory() as session:
            current = await session.scalar(
                select(ProductPrice).where(
                    ProductPrice.product_id == seeded["products"][sku]["id"],
                    ProductPrice.is_active.is_(True),
                )
            )
            current.is_active = False
            current.effective_to = current.effective_from
            session.add(
                ProductPrice(
                    product_id=seeded["products"][sku]["id"],
                    amount=Decimal("99.00"),
                    currency_code="IRT",
                    is_active=True,
                    source="INTEGRATION_TEST_NEW_PRICE",
                )
            )
            await session.commit()

    _db_run(change_price)
    fetched = api_client.get(f"/api/v1/sales/orders/{order_id}", headers=_headers("read-key-0001"))
    assert fetched.status_code == 200
    assert fetched.json()["lines"][0]["unit_price"] == "10.00"
    assert fetched.json()["subtotal"] == "40.00"

    cancel_headers = _headers("cancel-it-" + seeded["suffix"])
    cancelled = api_client.post(
        f"/api/v1/sales/orders/{order_id}/cancel",
        json={"reason": "عدم تأیید مشتری"},
        headers=cancel_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert all(a["status"] == "RELEASED" for a in cancelled.json()["lines"][0]["allocations"])

    cancelled_again = api_client.post(
        f"/api/v1/sales/orders/{order_id}/cancel",
        json={"reason": "تکرار درخواست لغو"},
        headers=cancel_headers,
    )
    assert cancelled_again.status_code == 200
    assert cancelled_again.json()["status"] == "CANCELLED"

    async def inspect(factory):
        async with factory() as session:
            balances = list(
                (
                    await session.scalars(
                        select(InventoryBalance).where(
                            InventoryBalance.product_id == seeded["products"][sku]["id"]
                        )
                    )
                ).all()
            )
            reservations = list(
                (
                    await session.scalars(
                        select(StockReservation).where(
                            StockReservation.reference_id == order_id
                        )
                    )
                ).all()
            )
            movement_count = await session.scalar(
                select(func.count(StockMovement.id)).where(
                    StockMovement.reference_id == order_id,
                    StockMovement.movement_type.in_(["RESERVE", "RELEASE"]),
                )
            )
            return balances, reservations, movement_count

    balances, reservations, movement_count = _db_run(inspect)
    assert {str(x.on_hand) for x in balances} == {"2.000", "3.000"}
    assert all(Decimal(x.reserved) == Decimal("0") for x in balances)
    assert len(reservations) == 2
    assert all(x.status == "RELEASED" for x in reservations)
    assert movement_count == 4


def test_failed_second_line_rolls_back_order_and_all_reservations(api_client):
    seeded = _seed_catalog(
        [
            {"price": "5.00", "stocks": {"A": "5", "B": "0"}},
            {"price": "7.00", "stocks": {"A": "1", "B": "0"}},
        ],
        ["A", "B"],
    )
    skus = list(seeded["products"])
    key = "rollback-" + seeded["suffix"]
    response = api_client.post(
        "/api/v1/sales/orders",
        json={"lines": [
            {"sku": skus[0], "quantity": "2"},
            {"sku": skus[1], "quantity": "2"},
        ]},
        headers=_headers(key),
    )
    assert response.status_code == 409

    async def inspect(factory):
        async with factory() as session:
            order_count = await session.scalar(
                select(func.count(SalesOrder.id)).where(SalesOrder.idempotency_key == key)
            )
            reservation_count = await session.scalar(
                select(func.count(StockReservation.id)).where(
                    StockReservation.reference_type == "SALES_ORDER",
                    StockReservation.idempotency_key.like("SO:%"),
                )
            )
            reserved = await session.scalar(
                select(func.sum(InventoryBalance.reserved)).where(
                    InventoryBalance.product_id.in_(
                        [x["id"] for x in seeded["products"].values()]
                    )
                )
            )
            return order_count, reservation_count, reserved

    order_count, _reservation_count, reserved = _db_run(inspect)
    assert order_count == 0
    assert Decimal(reserved or 0) == Decimal("0")


def test_missing_warehouse_balance_is_unknown_not_zero(api_client):
    seeded = _seed_catalog(
        [{"price": "12.00", "stocks": {"A": "1"}}],
        ["A", "B"],
    )
    sku = next(iter(seeded["products"]))
    response = api_client.post(
        "/api/v1/sales/orders",
        json={"lines": [{"sku": sku, "quantity": "2"}]},
        headers=_headers("unknown-" + seeded["suffix"]),
    )
    assert response.status_code == 409
    assert "نامشخص" in response.json()["detail"]


def test_concurrent_duplicate_key_and_last_unit_are_safe():
    seeded = _seed_catalog(
        [{"price": "15.00", "stocks": {"A": "1"}}],
        ["A"],
    )
    sku = next(iter(seeded["products"]))
    payload = SalesOrderCreate(lines=[{"sku": sku, "quantity": "1"}])

    async def run(factory):
        async def submit(key):
            async with factory() as session:
                try:
                    result = await create_sales_order(session, payload, key)
                    return result.id
                except HTTPException as exc:
                    return exc.status_code

        return await asyncio.gather(
            submit("same-idempotency-key"),
            submit("same-idempotency-key"),
            submit("different-order-key"),
        )

    results = _db_run(run)
    success_ids = [value for value in results if not isinstance(value, int)]
    conflict_codes = [value for value in results if isinstance(value, int)]
    assert len(success_ids) == 2
    assert success_ids[0] == success_ids[1]
    assert conflict_codes == [409]

    async def inspect(factory):
        async with factory() as session:
            balance = await session.scalar(
                select(InventoryBalance).where(
                    InventoryBalance.product_id == seeded["products"][sku]["id"]
                )
            )
            count = await session.scalar(select(func.count(SalesOrder.id)))
            return balance, count

    balance, count = _db_run(inspect)
    assert Decimal(balance.on_hand) == Decimal("1.000")
    assert Decimal(balance.reserved) == Decimal("1.000")
    assert count == 1
