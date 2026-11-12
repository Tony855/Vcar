import asyncio
from decimal import Decimal
import json
import uuid

import asyncpg
import pytest

from app.db.core import db
from app.db.repositories.order_repo import OrderRepository
from app.modules.payments.ledger import WalletLedger
from app.modules.payments.settlement import OrderSettlement, OrderNotSettleable, SettlementConflict


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


@pytest.mark.asyncio
async def test_order_status_outbox_is_transactional_and_retryable(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "outbox-passenger")
        driver_id = await _create_user(connection, "outbox-driver")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        async with database_pool.acquire() as connection:
            initial = await connection.fetchrow(
                """
                SELECT event_id, order_id, payload, attempts, delivered_at
                FROM order_event_outbox WHERE order_id=$1
                """,
                order["id"],
            )
        assert initial is not None
        assert initial["order_id"] == order["id"]
        assert json.loads(initial["payload"])["status"] == "DISPATCHING"
        assert initial["attempts"] == 0
        assert initial["delivered_at"] is None

        claimed = await OrderRepository.claim_status_events(limit=1)
        assert len(claimed) == 1
        assert claimed[0]["event_id"] == initial["event_id"]
        assert claimed[0]["attempts"] == 1
        assert await OrderRepository.mark_status_event_delivered(claimed[0]["id"], claimed[0]["lock_token"]) is True
        assert await OrderRepository.mark_status_event_delivered(claimed[0]["id"], claimed[0]["lock_token"]) is False

        assert await OrderRepository.accept(order["id"], driver_id) is True
        claimed = await OrderRepository.claim_status_events(limit=10)
        assert len(claimed) == 1
        assert json.loads(claimed[0]["payload"])["status"] == "ACCEPTED"
        assert await OrderRepository.retry_status_event(
            claimed[0]["id"], claimed[0]["lock_token"], "temporary Redis outage"
        ) is True

        async with database_pool.acquire() as connection:
            retry = await connection.fetchrow(
                """
                SELECT attempts, locked_at, available_at, created_at, last_error, delivered_at
                FROM order_event_outbox WHERE id=$1
                """,
                claimed[0]["id"],
            )
        assert retry["attempts"] == 1
        assert retry["locked_at"] is None
        assert retry["available_at"] > retry["created_at"]
        assert retry["last_error"] == "temporary Redis outage"
        assert retry["delivered_at"] is None
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_passenger_driver_order_and_wallet_lifecycle_is_consistent(database_pool):
    """Exercise the persisted passenger -> driver -> completed trip -> wallet path."""
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "e2e-passenger")
        driver_id = await _create_user(connection, "e2e-driver")
        wallet_id = await connection.fetchval(
            """
            INSERT INTO wallet_accounts (user_id, currency, balance_minor)
            VALUES ($1, 'USD', 0)
            RETURNING id
            """,
            passenger_id,
        )

    order = await OrderRepository.create(
        passenger_id, 104.9282, 11.5564, 104.9500, 11.5700, 5.0, 3.5
    )
    topup_key = f"e2e-topup-{uuid.uuid4()}"
    fare_key = f"e2e-fare-{uuid.uuid4()}"
    try:
        assert await OrderRepository.get(order["id"]) is not None
        assert await OrderRepository.accept(order["id"], driver_id) is True
        assert await OrderRepository.update_status(
            order["id"], "ARRIVED", "ACCEPTED",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True
        assert await OrderRepository.update_status(
            order["id"], "IN_PROGRESS", "ARRIVED",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True
        assert await OrderRepository.update_status(
            order["id"], "COMPLETED", "IN_PROGRESS",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True

        credit = await WalletLedger.post(
            user_id=passenger_id, currency="USD", amount_minor=1000,
            kind="TOPUP", idempotency_key=topup_key,
            metadata={"source": "integration-test"},
        )
        fare = await WalletLedger.post(
            user_id=passenger_id, currency="USD", amount_minor=-350,
            kind="TRIP_FARE", idempotency_key=fare_key,
            order_id=order["id"],
            metadata={"source": "completed-trip"},
        )
        replay = await WalletLedger.post(
            user_id=passenger_id, currency="USD", amount_minor=-350,
            kind="TRIP_FARE", idempotency_key=fare_key,
            order_id=order["id"],
            metadata={"source": "completed-trip"},
        )

        async with database_pool.acquire() as connection:
            persisted_status = await connection.fetchval(
                "SELECT status FROM orders WHERE id=$1", order["id"]
            )
            event_statuses = await connection.fetch(
                "SELECT to_status FROM order_events WHERE order_id=$1 ORDER BY id",
                order["id"],
            )
            outbox_count = await connection.fetchval(
                "SELECT count(*) FROM order_event_outbox WHERE order_id=$1",
                order["id"],
            )
            balance = await connection.fetchval(
                "SELECT balance_minor FROM wallet_accounts WHERE id=$1", wallet_id
            )
            fare_rows = await connection.fetchval(
                "SELECT count(*) FROM wallet_transactions WHERE order_id=$1 AND kind='TRIP_FARE'",
                order["id"],
            )

        assert persisted_status == "COMPLETED"
        assert [row["to_status"] for row in event_statuses] == [
            "DISPATCHING", "ACCEPTED", "ARRIVED", "IN_PROGRESS", "COMPLETED"
        ]
        assert outbox_count == 5
        assert credit.balance_minor == 1000
        assert fare.balance_minor == 650
        assert replay.replayed is True
        assert replay.transaction_id == fare.transaction_id
        assert balance == 650
        assert fare_rows == 1
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM wallet_transactions WHERE wallet_id=$1", wallet_id
            )
            await connection.execute("DELETE FROM wallet_accounts WHERE id=$1", wallet_id)
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_unapproved_driver_cannot_accept_order_even_if_called_directly(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "pending-passenger")
        driver_id = await _create_user(connection, "pending-driver")
        await connection.execute(
            "UPDATE users SET driver_status='PENDING' WHERE id=$1", driver_id
        )

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is False
        async with database_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT status, driver_id FROM orders WHERE id=$1", order["id"]
            )
            event_count = await connection.fetchval(
                "SELECT COUNT(*) FROM order_events WHERE order_id=$1", order["id"]
            )
        assert row["status"] == "DISPATCHING"
        assert row["driver_id"] is None
        assert event_count == 1
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_order_transition_rolls_back_when_outbox_append_fails(database_pool, monkeypatch):
    """The order row, event history and outbox must commit or roll back together."""
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "rollback-passenger")
        driver_id = await _create_user(connection, "rollback-driver")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        original_append = OrderRepository._append_event

        async def append_then_fail(connection, *args, **kwargs):
            await original_append(connection, *args, **kwargs)
            raise RuntimeError("injected outbox persistence failure")

        monkeypatch.setattr(OrderRepository, "_append_event", append_then_fail)
        with pytest.raises(RuntimeError, match="injected outbox persistence failure"):
            await OrderRepository.update_status(
                order["id"], "ARRIVED", "ACCEPTED",
                actor_user_id=driver_id, actor_role="DRIVER",
            )

        async with database_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT status FROM orders WHERE id=$1", order["id"]
            )
            event_statuses = await connection.fetch(
                "SELECT to_status FROM order_events WHERE order_id=$1 ORDER BY id",
                order["id"],
            )
            outbox_statuses = await connection.fetch(
                "SELECT payload->>'status' AS status FROM order_event_outbox WHERE order_id=$1 ORDER BY id",
                order["id"],
            )
        assert row["status"] == "ACCEPTED"
        assert [item["to_status"] for item in event_statuses] == ["DISPATCHING", "ACCEPTED"]
        assert [item["status"] for item in outbox_statuses] == ["DISPATCHING", "ACCEPTED"]
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_concurrent_outbox_workers_claim_disjoint_events(database_pool):
    """SKIP LOCKED workers must never receive the same pending outbox row."""
    async with database_pool.acquire() as connection:
        passenger_a = await _create_user(connection, "claim-passenger-a")
        passenger_b = await _create_user(connection, "claim-passenger-b")

    order_a = await OrderRepository.create(
        passenger_a, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    order_b = await OrderRepository.create(
        passenger_b, 104.9, 11.5, 104.96, 11.56, 6.0, 4.0
    )
    try:
        batches = await asyncio.gather(
            OrderRepository.claim_status_events(limit=1),
            OrderRepository.claim_status_events(limit=1),
        )
        claimed_ids = [row["id"] for batch in batches for row in batch]
        assert len(claimed_ids) == 2
        assert len(set(claimed_ids)) == 2
        async with database_pool.acquire() as connection:
            locked = await connection.fetchval(
                "SELECT COUNT(*) FROM order_event_outbox WHERE id = ANY($1) AND locked_at IS NOT NULL",
                claimed_ids,
            )
        assert locked == 2
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM orders WHERE id = ANY($1)", [order_a["id"], order_b["id"]]
            )
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_a, passenger_b]
            )


@pytest.mark.asyncio
async def test_outbox_worker_reclaims_claim_abandoned_by_crashed_process(database_pool):
    """A process crash must not strand an outbox event beyond the claim lease."""
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "lease-passenger")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        first_claim = await OrderRepository.claim_status_events(limit=1)
        assert len(first_claim) == 1
        outbox_id = first_claim[0]["id"]

        # Simulate a worker dying after claiming but before publishing/acking.
        async with database_pool.acquire() as connection:
            await connection.execute(
                "UPDATE order_event_outbox SET locked_at=now()-interval '6 minutes' WHERE id=$1",
                outbox_id,
            )

        recovered = await OrderRepository.claim_status_events(limit=1)
        assert len(recovered) == 1
        assert recovered[0]["id"] == outbox_id
        assert recovered[0]["attempts"] == first_claim[0]["attempts"] + 1

        # A late first worker cannot acknowledge or release the newer lease.
        assert await OrderRepository.mark_status_event_delivered(
            outbox_id, first_claim[0]["lock_token"]
        ) is False
        assert await OrderRepository.retry_status_event(
            outbox_id, first_claim[0]["lock_token"], "stale worker failure"
        ) is False

        async with database_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT locked_at, lock_token, delivered_at, attempts FROM order_event_outbox WHERE id=$1",
                outbox_id,
            )
        assert row["locked_at"] is not None
        assert row["lock_token"] == recovered[0]["lock_token"]
        assert row["delivered_at"] is None
        assert row["attempts"] == 2
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute("DELETE FROM users WHERE id=$1", passenger_id)


@pytest.mark.asyncio
async def test_outbox_retry_sanitizes_nul_and_truncates_error_text(database_pool):
    """PostgreSQL text cannot contain NUL bytes; retry metadata must stay writable."""
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "outbox-error-passenger")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        claimed = await OrderRepository.claim_status_events(limit=1)
        assert len(claimed) == 1
        error_text = "redis error\x00" + ("x" * 1200)
        assert await OrderRepository.retry_status_event(
            claimed[0]["id"], claimed[0]["lock_token"], error_text
        ) is True
        async with database_pool.acquire() as connection:
            stored = await connection.fetchval(
                "SELECT last_error FROM order_event_outbox WHERE id=$1",
                claimed[0]["id"],
            )
        assert stored is not None
        assert "\x00" not in stored
        assert len(stored) == 1000
        assert stored.startswith("redis error")
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute("DELETE FROM users WHERE id=$1", passenger_id)


@pytest.mark.asyncio
async def test_gps_location_rolls_back_when_trip_track_insert_fails(database_pool, monkeypatch):
    """A failed trip-track write must not leave an accepted but unannounced GPS point."""
    from datetime import datetime, timezone
    from fastapi import HTTPException

    from app.modules.locations import router as locations_router

    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "gps-rollback-passenger")
        driver_id = await _create_user(connection, "gps-rollback-driver")
        telegram_id = await connection.fetchval(
            "SELECT telegram_id FROM users WHERE id=$1", driver_id
        )
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    trigger_suffix = uuid.uuid4().hex[:12]
    function_name = f"vcar_fail_trip_track_{trigger_suffix}"
    trigger_name = f"vcar_fail_trip_track_trigger_{trigger_suffix}"
    async with database_pool.acquire() as connection:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        await connection.execute(
            f"""
            CREATE FUNCTION {function_name}() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'injected trip track write failure';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        await connection.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON trip_tracks "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )
    try:
        payload = locations_router.LocationUpdate(
            latitude=11.5564,
            longitude=104.9282,
            accuracy_m=5.0,
            recorded_at=datetime.now(timezone.utc),
        )
        with pytest.raises(Exception, match="injected trip track write failure"):
            await locations_router.update_driver_location(
                payload, {"sub": str(telegram_id)}
            )
        async with database_pool.acquire() as connection:
            location_count = await connection.fetchval(
                "SELECT COUNT(*) FROM driver_locations WHERE driver_id=$1",
                driver_id,
            )
            track_count = await connection.fetchval(
                "SELECT COUNT(*) FROM trip_tracks WHERE order_id=$1",
                order["id"],
            )
        assert location_count == 0
        assert track_count == 0
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON trip_tracks")
            await connection.execute(f"DROP FUNCTION IF EXISTS {function_name}()")
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_concurrent_gps_samples_keep_only_the_newest_timestamp(database_pool, monkeypatch):
    """Concurrent requests cannot let an older GPS sample overwrite a newer one."""
    from datetime import datetime, timedelta, timezone
    from fastapi import HTTPException

    from app.modules.locations import router as locations_router

    async with database_pool.acquire() as connection:
        driver_id = await _create_user(connection, "gps-concurrency-driver")
        telegram_id = await connection.fetchval(
            "SELECT telegram_id FROM users WHERE id=$1", driver_id
        )

    class RedisProjection:
        async def geoadd(self, *args):
            return 1

        async def zadd(self, *args):
            return 1

        async def zremrangebyscore(self, *args):
            return 0

        async def publish(self, *args):
            return 1

    monkeypatch.setattr(locations_router.db, "redis", RedisProjection())
    latest_time = datetime.now(timezone.utc) - timedelta(seconds=1)
    older_time = latest_time - timedelta(seconds=1)
    older = locations_router.LocationUpdate(
        latitude=11.5, longitude=104.9, recorded_at=older_time
    )
    newer = locations_router.LocationUpdate(
        latitude=11.6, longitude=105.0, recorded_at=latest_time
    )
    try:
        outcomes = await asyncio.gather(
            locations_router.update_driver_location(older, {"sub": str(telegram_id)}),
            locations_router.update_driver_location(newer, {"sub": str(telegram_id)}),
            return_exceptions=True,
        )
        accepted = [item for item in outcomes if isinstance(item, dict) and item.get("accepted")]
        rejected = [item for item in outcomes if isinstance(item, HTTPException)]
        assert len(accepted) in (1, 2)
        assert len(rejected) + len(accepted) == 2
        assert all(item.status_code == 409 for item in rejected)
        async with database_pool.acquire() as connection:
            stored = await connection.fetchval(
                "SELECT recorded_at FROM driver_locations WHERE driver_id=$1",
                driver_id,
            )
        assert stored == latest_time
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM driver_locations WHERE driver_id=$1", driver_id)
            await connection.execute("DELETE FROM users WHERE id=$1", driver_id)



@pytest.mark.asyncio
async def test_order_cancellation_binds_actor_role_to_the_matching_participant(database_pool):
    """A passenger cannot claim the driver role (or vice versa) to cancel."""
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "cancel-auth-passenger")
        driver_id = await _create_user(connection, "cancel-auth-driver")
        unrelated_id = await _create_user(connection, "cancel-auth-unrelated")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True

        for invalid_order_id, invalid_actor_id in (
            (0, passenger_id),
            (2**63, passenger_id),
            (order["id"], 0),
            (order["id"], 2**63),
            (order["id"], True),
        ):
            assert await OrderRepository.cancel_with_fee(
                invalid_order_id, invalid_actor_id, "PASSENGER", 1.0
            ) is False

        assert await OrderRepository.cancel_with_fee(
            order["id"], passenger_id, "DRIVER", 1.0
        ) is False
        assert await OrderRepository.cancel_with_fee(
            order["id"], driver_id, "PASSENGER", 1.0
        ) is False
        assert await OrderRepository.cancel_with_fee(
            order["id"], unrelated_id, "PASSENGER", 1.0
        ) is False
        assert await OrderRepository.cancel_with_fee(
            order["id"], passenger_id, "ADMIN", 0.0
        ) is False
        assert await OrderRepository.cancel_with_fee(
            order["id"], passenger_id, "PASSENGER", -1.0
        ) is False

        async with database_pool.acquire() as connection:
            status = await connection.fetchval(
                "SELECT status FROM orders WHERE id=$1", order["id"]
            )
            events = await connection.fetchval(
                "SELECT COUNT(*) FROM order_events WHERE order_id=$1", order["id"]
            )
        assert status == "ACCEPTED"
        assert events == 2

        assert await OrderRepository.cancel_with_fee(
            order["id"], passenger_id, "PASSENGER", 1.0
        ) is True
        async with database_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT status, cancel_by, cancel_fee FROM orders WHERE id=$1",
                order["id"],
            )
            last_event = await connection.fetchrow(
                "SELECT actor_user_id, actor_role, to_status FROM order_events "
                "WHERE order_id=$1 ORDER BY id DESC LIMIT 1",
                order["id"],
            )
        assert row["status"] == "CANCELLED"
        assert row["cancel_by"] == "PASSENGER"
        assert float(row["cancel_fee"]) == 1.0
        assert last_event["actor_user_id"] == passenger_id
        assert last_event["actor_role"] == "PASSENGER"
        assert last_event["to_status"] == "CANCELLED"
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)",
                [passenger_id, driver_id, unrelated_id],
            )



@pytest.mark.asyncio
async def test_driver_can_cancel_and_non_finite_fees_are_rejected(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "driver-cancel-passenger")
        driver_id = await _create_user(connection, "driver-cancel-driver")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        for invalid_fee in (float("nan"), float("inf"), float("-inf"), True, "1.0", object()):
            assert await OrderRepository.cancel_with_fee(
                order["id"], driver_id, "DRIVER", invalid_fee
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

        assert await OrderRepository.cancel_with_fee(
            order["id"], driver_id, "DRIVER", 0.0
        ) is True
        async with database_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT status, cancel_by, cancel_fee FROM orders WHERE id=$1",
                order["id"],
            )
            event = await connection.fetchrow(
                "SELECT actor_user_id, actor_role FROM order_events "
                "WHERE order_id=$1 ORDER BY id DESC LIMIT 1",
                order["id"],
            )
        assert row["status"] == "CANCELLED"
        assert row["cancel_by"] == "DRIVER"
        assert float(row["cancel_fee"]) == 0.0
        assert event["actor_user_id"] == driver_id
        assert event["actor_role"] == "DRIVER"
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_concurrent_order_cancellation_emits_exactly_one_event(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "cancel-race-passenger")
        driver_id = await _create_user(connection, "cancel-race-driver")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        results = await asyncio.gather(
            OrderRepository.cancel_with_fee(
                order["id"], passenger_id, "PASSENGER", 1.0
            ),
            OrderRepository.cancel_with_fee(
                order["id"], passenger_id, "PASSENGER", 1.0
            ),
        )
        assert sorted(results) == [False, True]
        async with database_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT status, cancel_by, cancel_fee FROM orders WHERE id=$1",
                order["id"],
            )
            cancellations = await connection.fetchval(
                "SELECT COUNT(*) FROM order_events "
                "WHERE order_id=$1 AND to_status='CANCELLED'",
                order["id"],
            )
            outbox_cancellations = await connection.fetchval(
                "SELECT COUNT(*) FROM order_event_outbox "
                "WHERE order_id=$1 AND payload->>'status'='CANCELLED'",
                order["id"],
            )
        assert row["status"] == "CANCELLED"
        assert row["cancel_by"] == "PASSENGER"
        assert float(row["cancel_fee"]) == 1.0
        assert cancellations == 1
        assert outbox_cancellations == 1
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_concurrent_duplicate_order_completion_emits_one_terminal_event(database_pool):
    """Repeated finish callbacks must produce one completion transition and outbox row."""
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "duplicate-finish-passenger")
        driver_id = await _create_user(connection, "duplicate-finish-driver")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        assert await OrderRepository.update_status(
            order["id"], "ARRIVED", "ACCEPTED",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True
        assert await OrderRepository.update_status(
            order["id"], "IN_PROGRESS", "ARRIVED",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True

        outcomes = await asyncio.gather(
            OrderRepository.update_status(
                order["id"], "COMPLETED", "IN_PROGRESS",
                actor_user_id=driver_id, actor_role="DRIVER",
            ),
            OrderRepository.update_status(
                order["id"], "COMPLETED", "IN_PROGRESS",
                actor_user_id=driver_id, actor_role="DRIVER",
            ),
        )
        assert sorted(outcomes) == [False, True]

        async with database_pool.acquire() as connection:
            status = await connection.fetchval(
                "SELECT status FROM orders WHERE id=$1", order["id"]
            )
            completed_events = await connection.fetchval(
                "SELECT count(*) FROM order_events WHERE order_id=$1 AND to_status='COMPLETED'",
                order["id"],
            )
            completed_outbox = await connection.fetchval(
                "SELECT count(*) FROM order_event_outbox WHERE order_id=$1 AND payload->>'status'='COMPLETED'",
                order["id"],
            )
        assert status == "COMPLETED"
        assert completed_events == 1
        assert completed_outbox == 1
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_order_ratings_are_authorized_idempotent_and_directional(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "rating-passenger")
        driver_id = await _create_user(connection, "rating-driver")
        unrelated_id = await _create_user(connection, "rating-unrelated")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        assert await OrderRepository.update_status(
            order["id"], "ARRIVED", "ACCEPTED",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True
        assert await OrderRepository.update_status(
            order["id"], "IN_PROGRESS", "ARRIVED",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True
        assert await OrderRepository.update_status(
            order["id"], "COMPLETED", "IN_PROGRESS",
            actor_user_id=driver_id, actor_role="DRIVER",
        ) is True

        # Unrelated users cannot rate, and ratings are unavailable before completion.
        assert await OrderRepository.submit_rating(
            order["id"], unrelated_id, "PASSENGER", 5
        ) is False
        assert await OrderRepository.submit_rating(
            order["id"], passenger_id, "DRIVER", 5
        ) is False
        for invalid_score in (0, 6, True, "5"):
            assert await OrderRepository.submit_rating(
                order["id"], passenger_id, "PASSENGER", invalid_score
            ) is False
        for invalid_order_id, invalid_actor_id in (
            (0, passenger_id),
            (2**63, passenger_id),
            (order["id"], 0),
            (order["id"], 2**63),
            (order["id"], True),
        ):
            assert await OrderRepository.submit_rating(
                invalid_order_id, invalid_actor_id, "PASSENGER", 5
            ) is False

        # Each participant can rate the other once; a duplicate is a no-op.
        assert await OrderRepository.submit_rating(
            order["id"], passenger_id, "PASSENGER", 5
        ) is True
        assert await OrderRepository.submit_rating(
            order["id"], passenger_id, "PASSENGER", 1
        ) is False
        assert await OrderRepository.submit_rating(
            order["id"], driver_id, "DRIVER", 4
        ) is True
        assert await OrderRepository.submit_rating(
            order["id"], driver_id, "DRIVER", 1
        ) is False

        async with database_pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT rater_role, rater_user_id, ratee_user_id, score "
                "FROM order_ratings WHERE order_id=$1 ORDER BY rater_role",
                order["id"],
            )
            aggregates = await connection.fetch(
                "SELECT id, rating, rating_count FROM users WHERE id = ANY($1)",
                [passenger_id, driver_id],
            )
        assert [(r["rater_role"], r["score"]) for r in rows] == [
            ("DRIVER", 4), ("PASSENGER", 5)
        ]
        assert all(r["rater_user_id"] != r["ratee_user_id"] for r in rows)
        by_id = {r["id"]: r for r in aggregates}
        assert float(by_id[driver_id]["rating"]) == 5.0
        assert by_id[driver_id]["rating_count"] == 2
        assert float(by_id[passenger_id]["rating"]) == 4.5
        assert by_id[passenger_id]["rating_count"] == 2
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM reviews WHERE order_id=$1", order["id"])
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)",
                [passenger_id, driver_id, unrelated_id],
            )


@pytest.mark.asyncio
async def test_concurrent_duplicate_order_rating_updates_aggregate_once(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "rating-race-passenger")
        driver_id = await _create_user(connection, "rating-race-driver")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        for new_status, old_status in (
            ("ARRIVED", "ACCEPTED"),
            ("IN_PROGRESS", "ARRIVED"),
            ("COMPLETED", "IN_PROGRESS"),
        ):
            assert await OrderRepository.update_status(
                order["id"], new_status, old_status,
                actor_user_id=driver_id, actor_role="DRIVER",
            ) is True

        results = await asyncio.gather(
            OrderRepository.submit_rating(order["id"], passenger_id, "PASSENGER", 5),
            OrderRepository.submit_rating(order["id"], passenger_id, "PASSENGER", 5),
        )
        assert sorted(results) == [False, True]
        async with database_pool.acquire() as connection:
            count = await connection.fetchval(
                "SELECT count(*) FROM order_ratings WHERE order_id=$1 AND rater_role='PASSENGER'",
                order["id"],
            )
            aggregate = await connection.fetchrow(
                "SELECT rating, rating_count FROM users WHERE id=$1", driver_id
            )
        assert count == 1
        assert float(aggregate["rating"]) == 5.0
        assert aggregate["rating_count"] == 2
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM reviews WHERE order_id=$1", order["id"])
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_rating_comment_is_bound_to_own_completed_order_and_persists(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "rating-comment-passenger")
        other_passenger_id = await _create_user(connection, "rating-comment-other")
        driver_id = await _create_user(connection, "rating-comment-driver")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        for new_status, old_status in (
            ("ARRIVED", "ACCEPTED"),
            ("IN_PROGRESS", "ARRIVED"),
            ("COMPLETED", "IN_PROGRESS"),
        ):
            assert await OrderRepository.update_status(
                order["id"], new_status, old_status,
                actor_user_id=driver_id, actor_role="DRIVER",
            ) is True
        assert await OrderRepository.submit_rating(
            order["id"], passenger_id, "PASSENGER", 5
        ) is True

        assert await OrderRepository.add_rating_comment(
            order["id"], other_passenger_id, "forged"
        ) is False
        assert await OrderRepository.add_rating_comment(
            order["id"], passenger_id, "   "
        ) is False
        assert await OrderRepository.add_rating_comment(
            order["id"], passenger_id, "x" * 1001
        ) is False
        assert await OrderRepository.add_rating_comment(
            order["id"], passenger_id, "Smooth trip"
        ) is True
        # A repeated callback must not silently replace the passenger's first comment.
        assert await OrderRepository.add_rating_comment(
            order["id"], passenger_id, "replacement comment"
        ) is False
        assert await OrderRepository.add_rating_comment(
            2**63, passenger_id, "out-of-range order"
        ) is False
        assert await OrderRepository.add_rating_comment(
            order["id"], 2**63, "out-of-range passenger"
        ) is False

        async with database_pool.acquire() as connection:
            comment = await connection.fetchval(
                "SELECT comment FROM order_ratings "
                "WHERE order_id=$1 AND rater_role='PASSENGER'",
                order["id"],
            )
            legacy = await connection.fetchrow(
                "SELECT driver_id, passenger_id, rating FROM reviews WHERE order_id=$1",
                order["id"],
            )
        assert comment == "Smooth trip"
        assert legacy["driver_id"] == driver_id
        assert legacy["passenger_id"] == passenger_id
        assert legacy["rating"] == 5
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM reviews WHERE order_id=$1", order["id"])
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)",
                [passenger_id, other_passenger_id, driver_id],
            )


@pytest.mark.asyncio
async def test_rating_legacy_projection_rolls_back_with_rating_insert(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "rating-projection-passenger")
        driver_id = await _create_user(connection, "rating-projection-driver")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        for new_status, old_status in (
            ("ARRIVED", "ACCEPTED"),
            ("IN_PROGRESS", "ARRIVED"),
            ("COMPLETED", "IN_PROGRESS"),
        ):
            assert await OrderRepository.update_status(
                order["id"], new_status, old_status,
                actor_user_id=driver_id, actor_role="DRIVER",
            ) is True
        async with database_pool.acquire() as connection:
            await connection.execute(
                """
                CREATE FUNCTION reject_rating_projection() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'injected rating projection failure';
                END;
                $$ LANGUAGE plpgsql
                """
            )
            await connection.execute(
                """
                CREATE TRIGGER reject_rating_projection
                BEFORE INSERT ON reviews
                FOR EACH ROW EXECUTE FUNCTION reject_rating_projection()
                """
            )
        with pytest.raises(Exception, match="injected rating projection failure"):
            await OrderRepository.submit_rating(
                order["id"], passenger_id, "PASSENGER", 4
            )
        async with database_pool.acquire() as connection:
            rating_count = await connection.fetchval(
                "SELECT count(*) FROM order_ratings WHERE order_id=$1",
                order["id"],
            )
            user_rating = await connection.fetchrow(
                "SELECT rating, rating_count FROM users WHERE id=$1", driver_id
            )
        assert rating_count == 0
        assert float(user_rating["rating"]) == 5.0
        assert user_rating["rating_count"] == 1
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DROP TRIGGER IF EXISTS reject_rating_projection ON reviews")
            await connection.execute("DROP FUNCTION IF EXISTS reject_rating_projection()")
            await connection.execute("DELETE FROM reviews WHERE order_id=$1", order["id"])
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )


@pytest.mark.asyncio
async def test_completed_order_settlement_snapshot_is_atomic_and_idempotent(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "settlement-passenger")
        driver_id = await _create_user(connection, "settlement-driver")
    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    key = f"settlement-{uuid.uuid4()}"
    try:
        with pytest.raises(OrderNotSettleable):
            await OrderSettlement.prepare(
                order_id=order["id"], currency="USD", policy_version="kh-v1",
                idempotency_key=key, components={"driver_earning": 800, "platform_fee": 200},
            )
        assert await OrderRepository.accept(order["id"], driver_id) is True
        for new_status, old_status in (("ARRIVED", "ACCEPTED"), ("IN_PROGRESS", "ARRIVED"), ("COMPLETED", "IN_PROGRESS")):
            assert await OrderRepository.update_status(
                order["id"], new_status, old_status,
                actor_user_id=driver_id, actor_role="DRIVER",
            ) is True

        args = dict(
            order_id=order["id"], currency="USD", policy_version="kh-v1",
            idempotency_key=key, components={"driver_earning": 800, "platform_fee": 200},
        )
        prepared = await OrderSettlement.prepare(**args)
        replay = await OrderSettlement.prepare(**args)
        assert prepared.status == "PREPARED" and prepared.replayed is False
        assert replay.settlement_id == prepared.settlement_id and replay.replayed is True

        with pytest.raises(SettlementConflict):
            await OrderSettlement.prepare(
                **{**args, "components": {"driver_earning": 700, "platform_fee": 300}}
            )
        async with database_pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT id, currency, policy_version, components, status FROM order_settlements WHERE order_id=$1",
                order["id"],
            )
        assert len(rows) == 1
        assert rows[0]["status"] == "PREPARED"
        persisted_components = rows[0]["components"]
        if isinstance(persisted_components, str):
            persisted_components = json.loads(persisted_components)
        assert persisted_components == {"driver_earning": 800, "platform_fee": 200}
        with pytest.raises(asyncpg.PostgresError, match="snapshot is immutable"):
            async with database_pool.acquire() as connection:
                await connection.execute(
                    "UPDATE order_settlements SET components='{}'::jsonb WHERE order_id=$1",
                    order["id"],
                )
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM order_settlements WHERE order_id=$1", order["id"])
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute("DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id])


@pytest.mark.asyncio
async def test_cancellation_fee_preserves_exact_decimal_without_float_rounding(database_pool):
    async with database_pool.acquire() as connection:
        passenger_id = await _create_user(connection, "decimal-cancel-passenger")
        driver_id = await _create_user(connection, "decimal-cancel-driver")

    order = await OrderRepository.create(
        passenger_id, 104.9, 11.5, 104.95, 11.55, 5.0, 3.5
    )
    fee = Decimal("0.100000000000000001")
    try:
        assert await OrderRepository.accept(order["id"], driver_id) is True
        assert await OrderRepository.cancel_with_fee(
            order["id"], passenger_id, "PASSENGER", fee
        ) is True
        async with database_pool.acquire() as connection:
            stored_fee = await connection.fetchval(
                "SELECT cancel_fee FROM orders WHERE id=$1", order["id"]
            )
            event_metadata = await connection.fetchval(
                "SELECT metadata FROM order_events WHERE order_id=$1 "
                "AND to_status='CANCELLED' ORDER BY id DESC LIMIT 1",
                order["id"],
            )
        assert stored_fee == fee
        if isinstance(event_metadata, str):
            event_metadata = json.loads(event_metadata)
        assert event_metadata["cancel_fee"] == "0.100000000000000001"
    finally:
        async with database_pool.acquire() as connection:
            await connection.execute("DELETE FROM orders WHERE id=$1", order["id"])
            await connection.execute(
                "DELETE FROM users WHERE id = ANY($1)", [passenger_id, driver_id]
            )
