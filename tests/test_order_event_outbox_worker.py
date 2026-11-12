from unittest.mock import AsyncMock

import pytest

from app.services.order_event_outbox import dispatch_order_event_batch


@pytest.mark.asyncio
async def test_outbox_worker_publishes_event_and_acknowledges_it():
    event = {
        "id": 7,
        "event_id": 42,
        "order_id": 9,
        "payload": {
            "event_id": 42,
            "order_id": 9,
            "status": "ACCEPTED",
        },
    }
    repository = type("Repository", (), {})()
    repository.claim_status_events = AsyncMock(return_value=[event])
    repository.mark_status_event_delivered = AsyncMock(return_value=True)
    repository.retry_status_event = AsyncMock()
    redis_client = type("Redis", (), {})()
    redis_client.publish = AsyncMock(return_value=1)

    delivered = await dispatch_order_event_batch(redis_client, repository)

    assert delivered == 1
    redis_client.publish.assert_awaited_once()
    channel, raw_payload = redis_client.publish.await_args.args
    assert channel == "order:status:9"
    import json
    payload = json.loads(raw_payload)
    assert payload["event_id"] == 42
    assert payload["status"] == "ACCEPTED"
    repository.mark_status_event_delivered.assert_awaited_once_with(7)
    repository.retry_status_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbox_worker_retries_when_redis_publish_fails():
    event = {
        "id": 8,
        "event_id": 43,
        "order_id": 10,
        "payload": '{"status":"ARRIVED"}',
    }
    repository = type("Repository", (), {})()
    repository.claim_status_events = AsyncMock(return_value=[event])
    repository.mark_status_event_delivered = AsyncMock()
    repository.retry_status_event = AsyncMock(return_value=True)
    redis_client = type("Redis", (), {})()
    redis_client.publish = AsyncMock(side_effect=ConnectionError("redis unavailable"))

    delivered = await dispatch_order_event_batch(redis_client, repository)

    assert delivered == 0
    repository.mark_status_event_delivered.assert_not_awaited()
    repository.retry_status_event.assert_awaited_once()
    outbox_id, error = repository.retry_status_event.await_args.args
    assert outbox_id == 8
    assert isinstance(error, ConnectionError)


@pytest.mark.asyncio
async def test_outbox_worker_recovers_event_id_for_legacy_payload():
    event = {"id": 9, "event_id": 44, "order_id": 11, "payload": "{}"}
    repository = type("Repository", (), {})()
    repository.claim_status_events = AsyncMock(return_value=[event])
    repository.mark_status_event_delivered = AsyncMock(return_value=True)
    repository.retry_status_event = AsyncMock()
    redis_client = type("Redis", (), {})()
    redis_client.publish = AsyncMock(return_value=1)

    assert await dispatch_order_event_batch(redis_client, repository) == 1
    import json
    payload = json.loads(redis_client.publish.await_args.args[1])
    assert payload["event_id"] == 44
    assert payload["order_id"] == 11


@pytest.mark.asyncio
async def test_outbox_worker_uses_canonical_database_event_and_order_ids():
    event = {
        "id": 10,
        "event_id": 50,
        "order_id": 12,
        "payload": {"event_id": 999, "order_id": 777, "status": "IN_PROGRESS"},
    }
    repository = type("Repository", (), {})()
    repository.claim_status_events = AsyncMock(return_value=[event])
    repository.mark_status_event_delivered = AsyncMock(return_value=True)
    repository.retry_status_event = AsyncMock()
    redis_client = type("Redis", (), {})()
    redis_client.publish = AsyncMock(return_value=1)

    assert await dispatch_order_event_batch(redis_client, repository) == 1
    channel, raw_payload = redis_client.publish.await_args.args
    import json
    payload = json.loads(raw_payload)
    assert channel == "order:status:12"
    assert payload["event_id"] == 50
    assert payload["order_id"] == 12
    assert payload["status"] == "IN_PROGRESS"
    repository.mark_status_event_delivered.assert_awaited_once_with(10)
    repository.retry_status_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbox_worker_does_not_count_unacknowledged_publish_as_delivered():
    event = {
        "id": 11,
        "event_id": 51,
        "order_id": 13,
        "payload": {"status": "COMPLETED"},
    }
    repository = type("Repository", (), {})()
    repository.claim_status_events = AsyncMock(return_value=[event])
    repository.mark_status_event_delivered = AsyncMock(return_value=False)
    repository.retry_status_event = AsyncMock()
    redis_client = type("Redis", (), {})()
    redis_client.publish = AsyncMock(return_value=1)

    delivered = await dispatch_order_event_batch(redis_client, repository)

    assert delivered == 0
    redis_client.publish.assert_awaited_once()
    repository.mark_status_event_delivered.assert_awaited_once_with(11)
    repository.retry_status_event.assert_not_awaited()
