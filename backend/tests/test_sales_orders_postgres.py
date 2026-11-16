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
from app.main import app
from app.models.inventory import InventoryBalance, StockMovement, StockReservation
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.sales_order import SalesOrder, SalesOrderEvent, SalesOrderLine
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
        json={"reason": "عدم تأیید مشتری"},
        headers=cancel_headers,
    )
    assert cancelled_again.status_code == 200
    assert cancelled_again.json()["status"] == "CANCELLED"

    reused_key_conflict = api_client.post(
        f"/api/v1/sales/orders/{order_id}/cancel",
        json={"reason": "دلیل متفاوت"},
        headers=cancel_headers,
    )
    assert reused_key_conflict.status_code == 409

    new_key_conflict = api_client.post(
        f"/api/v1/sales/orders/{order_id}/cancel",
        json={"reason": "عدم تأیید مشتری"},
        headers=_headers("cancel-new-" + seeded["suffix"]),
    )
    assert new_key_conflict.status_code == 409

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
            event_count = await session.scalar(
                select(func.count(SalesOrderEvent.id)).where(
                    SalesOrderEvent.order_id == uuid.UUID(order_id)
                )
            )
            return balances, reservations, movement_count, event_count

    balances, reservations, movement_count, event_count = _db_run(inspect)
    assert {str(x.on_hand) for x in balances} == {"2.000", "3.000"}
    assert all(Decimal(x.reserved) == Decimal("0") for x in balances)
    assert len(reservations) == 2
    assert all(x.status == "RELEASED" for x in reservations)
    assert movement_count == 4
    assert event_count == 2


def test_failed_second_line_rolls_back_order_reservations_and_movements(api_client):
    seeded = _seed_catalog(
        [
            {"price": "5.00", "stocks": {"A": "5", "B": "0"}},
            {"price": "7.00", "stocks": {"A": "1", "B": "0"}},
        ],
        ["A", "B"],
    )
    skus = list(seeded["products"])
    product_ids = [x["id"] for x in seeded["products"].values()]
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
                    StockReservation.product_id.in_(product_ids),
                )
            )
            movement_count = await session.scalar(
                select(func.count(StockMovement.id)).where(
                    StockMovement.reference_type == "SALES_ORDER",
                    StockMovement.product_id.in_(product_ids),
                )
            )
            balances = list(
                (
                    await session.scalars(
                        select(InventoryBalance).where(
                            InventoryBalance.product_id.in_(product_ids)
                        )
                    )
                ).all()
            )
            return order_count, reservation_count, movement_count, balances

    order_count, reservation_count, movement_count, balances = _db_run(inspect)
    assert order_count == 0
    assert reservation_count == 0
    assert movement_count == 0
    assert sorted(Decimal(x.on_hand) for x in balances) == [
        Decimal("0.000"),
        Decimal("0.000"),
        Decimal("1.000"),
        Decimal("5.000"),
    ]
    assert all(Decimal(x.reserved) == Decimal("0") for x in balances)


def test_missing_warehouse_balance_is_unknown_and_never_created(api_client):
    seeded = _seed_catalog(
        [{"price": "12.00", "stocks": {"A": "1"}}],
        ["A", "B"],
    )
    sku = next(iter(seeded["products"]))
    missing_warehouse_id = next(
        value["id"]
        for key, value in seeded["warehouses"].items()
        if key.endswith("-B")
    )
    response = api_client.post(
        "/api/v1/sales/orders",
        json={"lines": [{"sku": sku, "quantity": "2"}]},
        headers=_headers("unknown-" + seeded["suffix"]),
    )
    assert response.status_code == 409
    assert "نامشخص" in response.json()["detail"]

    async def inspect(factory):
        async with factory() as session:
            return await session.scalar(
                select(func.count(InventoryBalance.product_id)).where(
                    InventoryBalance.product_id == seeded["products"][sku]["id"],
                    InventoryBalance.warehouse_id == missing_warehouse_id,
                )
            )

    assert _db_run(inspect) == 0


def test_concurrent_same_key_is_idempotent_and_different_keys_cannot_oversell():
    same_key_seed = _seed_catalog(
        [{"price": "15.00", "stocks": {"A": "1"}}],
        ["A"],
    )
    same_sku = next(iter(same_key_seed["products"]))
    same_payload = SalesOrderCreate(lines=[{"sku": same_sku, "quantity": "1"}])

    async def submit(factory, payload, key):
        async with factory() as session:
            try:
                result = await create_sales_order(session, payload, key)
                return result.id
            except HTTPException as exc:
                return exc.status_code

    async def same_key_run(factory):
        return await asyncio.gather(
            submit(factory, same_payload, "same-key-" + same_key_seed["suffix"]),
            submit(factory, same_payload, "same-key-" + same_key_seed["suffix"]),
        )

    same_results = _db_run(same_key_run)
    same_success_ids = [value for value in same_results if not isinstance(value, int)]
    assert len(same_success_ids) == 2
    assert same_success_ids[0] == same_success_ids[1]

    different_key_seed = _seed_catalog(
        [{"price": "20.00", "stocks": {"A": "1"}}],
        ["A"],
    )
    different_sku = next(iter(different_key_seed["products"]))
    different_payload = SalesOrderCreate(lines=[{"sku": different_sku, "quantity": "1"}])

    async def different_key_run(factory):
        return await asyncio.gather(
            submit(factory, different_payload, "order-a-" + different_key_seed["suffix"]),
            submit(factory, different_payload, "order-b-" + different_key_seed["suffix"]),
        )

    different_results = _db_run(different_key_run)
    different_success_ids = [
        value for value in different_results if not isinstance(value, int)
    ]
    conflicts = [value for value in different_results if isinstance(value, int)]
    assert len(different_success_ids) == 1
    assert conflicts == [409]

    async def inspect(factory, seeded, sku):
        async with factory() as session:
            balance = await session.scalar(
                select(InventoryBalance).where(
                    InventoryBalance.product_id == seeded["products"][sku]["id"]
                )
            )
            count = await session.scalar(
                select(func.count(SalesOrderLine.id)).where(
                    SalesOrderLine.product_id == seeded["products"][sku]["id"]
                )
            )
            return balance, count

    # The same-key pair creates exactly one order and reserves one unit.
    same_balance, same_order_count = _db_run(
        lambda factory: inspect(factory, same_key_seed, same_sku)
    )
    assert Decimal(same_balance.on_hand) == Decimal("1.000")
    assert Decimal(same_balance.reserved) == Decimal("1.000")
    assert same_order_count == 1

    different_balance, different_order_count = _db_run(
        lambda factory: inspect(factory, different_key_seed, different_sku)
    )
    assert Decimal(different_balance.on_hand) == Decimal("1.000")
    assert Decimal(different_balance.reserved) == Decimal("1.000")
    assert different_order_count == 1


def test_explicit_warehouse_is_respected_without_fallback(api_client):
    seeded = _seed_catalog(
        [{"price": "30.00", "stocks": {"A": "3", "B": "1"}}],
        ["A", "B"],
    )
    sku = next(iter(seeded["products"]))
    warehouse_a = next(
        value["id"] for key, value in seeded["warehouses"].items() if key.endswith("-A")
    )
    warehouse_b = next(
        value["id"] for key, value in seeded["warehouses"].items() if key.endswith("-B")
    )

    insufficient = api_client.post(
        "/api/v1/sales/orders",
        json={
            "warehouse_id": warehouse_b,
            "lines": [{"sku": sku, "quantity": "2"}],
        },
        headers=_headers("selected-b-" + seeded["suffix"]),
    )
    assert insufficient.status_code == 409

    created = api_client.post(
        "/api/v1/sales/orders",
        json={
            "warehouse_id": warehouse_b,
            "lines": [
                {"sku": sku, "quantity": "2", "warehouse_id": warehouse_a}
            ],
        },
        headers=_headers("line-override-" + seeded["suffix"]),
    )
    assert created.status_code == 201, created.text
    allocations = created.json()["lines"][0]["allocations"]
    assert len(allocations) == 1
    assert allocations[0]["warehouse_id"] == str(warehouse_a)
    assert Decimal(allocations[0]["quantity"]) == Decimal("2.000")
