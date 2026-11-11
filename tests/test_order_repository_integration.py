import asyncio
import uuid

import asyncpg
import pytest

from app.db.core import db
from app.db.repositories.order_repo import OrderRepository


@pytest.fixture
async def database_pool(monkeypatch):
    import os
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is not configured for integration tests")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    monkeypatch.setattr(db, "pool", pool)
    yield pool
    await pool.close()


async def _create_user(connection, suffix):
    row = await connection.fetchrow(
        """
        INSERT INTO users (telegram_id, first_name, driver_status)
        VALUES ($1, $2, 'APPROVED')
        RETURNING id
        """,
        int(uuid.uuid4().int % 8_000_000_000) + 1,
        suffix,
    )
    return row["id"]


@pytest.mark.asyncio
async def test_two_drivers_cannot_accept_same_order(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "passenger")
        driver_a = await _create_user(connection, "driver-a")
        driver_b = await _create_user(connection, "driver-b")

    order = await OrderRepository.create(passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5)
    outcomes = await asyncio.gather(
        OrderRepository.accept(order["id"], driver_a),
        OrderRepository.accept(order["id"], driver_b),
    )
    assert sorted(outcomes) == [False, True]

    async with database_pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, driver_id FROM orders WHERE id=$1", order["id"]
        )
        events = await connection.fetchval(
            "SELECT COUNT(*) FROM order_events WHERE order_id=$1", order["id"]
        )
        assert row["status"] == "ACCEPTED"
        assert row["driver_id"] in (driver_a, driver_b)
        assert events == 2
        await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
        await connection.execute("DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_a, driver_b])


@pytest.mark.asyncio
async def test_one_driver_cannot_accept_two_active_orders(database_pool):
    async with database_pool.acquire() as connection:
        passenger_a = await _create_user(connection, "passenger-a")
        passenger_b = await _create_user(connection, "passenger-b")
        driver = await _create_user(connection, "driver")

    order_a = await OrderRepository.create(passenger_a, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5)
    order_b = await OrderRepository.create(passenger_b, 104.9, 11.5, 104.96, 11.56, 6.0, 4.0)
    outcomes = await asyncio.gather(
        OrderRepository.accept(order_a["id"], driver),
        OrderRepository.accept(order_b["id"], driver),
    )
    assert sorted(outcomes) == [False, True]

    async with database_pool.acquire() as connection:
        active_count = await connection.fetchval(
            """
            SELECT COUNT(*) FROM orders
            WHERE driver_id=$1 AND status IN ('ACCEPTED','ARRIVED','IN_PROGRESS')
            """,
            driver,
        )
        assert active_count == 1
        await connection.execute("DELETE FROM orders WHERE id = ANY($1)", [order_a["id"], order_b["id"]])
        await connection.execute("DELETE FROM users WHERE id = ANY($1)", [passenger_a, passenger_b, driver])


@pytest.mark.asyncio
async def test_only_assigned_driver_can_advance_order(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "ownership-passenger")
        assigned_driver = await _create_user(connection, "ownership-assigned")
        other_driver = await _create_user(connection, "ownership-other")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], assigned_driver) is True

        # A different authenticated driver and an unbound human actor cannot
        # advance the order even when the expected status is correct.
        assert await OrderRepository.update_status(
            order["id"], "ARRIVED", "ACCEPTED",
            actor_user_id=other_driver, actor_role="DRIVER",
        ) is False
        assert await OrderRepository.update_status(
            order["id"], "ARRIVED", "ACCEPTED",
            actor_role="DRIVER",
        ) is False

        async with database_pool.acquire() as connection:
            status = await connection.fetchval(
                "SELECT status FROM orders WHERE id=$1", order["id"]
            )
            event_count = await connection.fetchval(
                "SELECT COUNT(*) FROM order_events WHERE order_id=$1", order["id"]
            )
        assert status == "ACCEPTED"
        assert event_count == 2

        assert await OrderRepository.update_status(
            order["id"], "ARRIVED", "ACCEPTED",
            actor_user_id=assigned_driver, actor_role="DRIVER",
        ) is True
        async with database_pool.acquire() as connection:
            status = await connection.fetchval(
                "SELECT status FROM orders WHERE id=$1", order["id"]
            )
            event_count = await connection.fetchval(
                "SELECT COUNT(*) FROM order_events WHERE order_id=$1", order["id"]
            )
        assert status == "ARRIVED"
        assert event_count == 3
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)",
                [passenger_id, assigned_driver, other_driver],
            )
