import pytest

from app.db.core import db
from app.db.repositories.order_repo import OrderRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_id", "driver_id"),
    [
        (0, 1), (-1, 1), (True, 1), (2**63, 1),
        (1, 0), (1, -1), (1, True), (1, 2**63),
        ("1", 1), (1, "1"),
    ],
)
async def test_accept_rejects_invalid_bigint_identifiers_before_database(
    monkeypatch, order_id, driver_id
):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.accept(order_id, driver_id) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", [0, -1, True, 2**63, "1"])
async def test_update_status_rejects_invalid_order_id_before_database(monkeypatch, order_id):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.update_status(
        order_id, "ARRIVED", "ACCEPTED", actor_user_id=2, actor_role="DRIVER"
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_id", [0, -1, True, 2**63, "2", 2.0])
async def test_update_status_rejects_invalid_human_actor_before_database(monkeypatch, actor_id):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.update_status(
        1, "ARRIVED", "ACCEPTED", actor_user_id=actor_id, actor_role="DRIVER"
    ) is False


@pytest.mark.asyncio
async def test_update_status_system_actor_must_not_supply_user_identity(monkeypatch):
    monkeypatch.setattr(db, "pool", None)
    assert await OrderRepository.update_status(
        1, "ARRIVED", "ACCEPTED", actor_user_id=2, actor_role="SYSTEM"
    ) is False



@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 501, True, 1.5, "10", None])
async def test_claim_status_events_rejects_invalid_limit_before_database(monkeypatch, limit):
    monkeypatch.setattr(db, "pool", None)
    with pytest.raises(ValueError, match="claim limit"):
        await OrderRepository.claim_status_events(limit=limit)


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


class _OutboxUpdateConnection:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("result,expected", [("UPDATE 1", True), ("UPDATE 0", False)])
async def test_outbox_acknowledgement_is_fenced_by_claim_token(
    monkeypatch, result, expected
):
    connection = _OutboxUpdateConnection(result)
    monkeypatch.setattr(db, "pool", _Pool(connection))

    acknowledged = await OrderRepository.mark_status_event_delivered(
        123, "claim-token-current"
    )

    assert acknowledged is expected
    query, args = connection.calls[0]
    assert "lock_token=$2" in query
    assert args == (123, "claim-token-current")


@pytest.mark.asyncio
@pytest.mark.parametrize("result,expected", [("UPDATE 1", True), ("UPDATE 0", False)])
async def test_outbox_retry_release_is_fenced_by_claim_token(
    monkeypatch, result, expected
):
    connection = _OutboxUpdateConnection(result)
    monkeypatch.setattr(db, "pool", _Pool(connection))

    released = await OrderRepository.retry_status_event(
        123, "claim-token-current", RuntimeError("temporary failure")
    )

    assert released is expected
    query, args = connection.calls[0]
    assert "lock_token=$3" in query
    assert args[0] == 123
    assert args[2] == "claim-token-current"
    assert args[1] == "temporary failure"
