import pytest

from app.modules.payments import settlement


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, order_status="COMPLETED", previous=None):
        self.order_status = order_status
        self.previous = previous
        self.calls = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "SELECT 1"

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        if "FROM orders" in query:
            return {"id": args[0], "status": self.order_status}
        if "FROM order_settlements" in query:
            return self.previous
        if "INSERT INTO order_settlements" in query:
            return {"id": 91, "status": "PREPARED"}
        raise AssertionError(f"unexpected query: {query}")


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return Acquire(self.connection)


@pytest.mark.asyncio
async def test_prepare_creates_snapshot_only_for_completed_order(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))

    result = await settlement.OrderSettlement.prepare(
        order_id=12, currency="USD", policy_version="kh-v1",
        idempotency_key="settlement-order-12-v1",
        components={"driver_earning": 800, "platform_fee": 200},
    )

    assert result.settlement_id == 91
    assert result.status == "PREPARED"
    assert result.replayed is False
    insert = [call for call in conn.calls if call[0] == "fetchrow" and "INSERT INTO order_settlements" in call[1]]
    assert len(insert) == 1
    assert '"driver_earning":800' in insert[0][2][4]


@pytest.mark.asyncio
async def test_prepare_replays_identical_snapshot_without_second_insert(monkeypatch):
    previous = {
        "id": 91, "order_id": 12, "currency": "USD",
        "policy_version": "kh-v1", "idempotency_key": "settlement-order-12-v1",
        "components": {"driver_earning": 800, "platform_fee": 200},
        "status": "PREPARED",
    }
    conn = FakeConnection(previous=previous)
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))

    result = await settlement.OrderSettlement.prepare(
        order_id=12, currency="USD", policy_version="kh-v1",
        idempotency_key="settlement-order-12-v1",
        components={"platform_fee": 200, "driver_earning": 800},
    )

    assert result.replayed is True
    assert result.settlement_id == 91
    assert not any("INSERT INTO order_settlements" in call[1] for call in conn.calls if call[0] == "fetchrow")


@pytest.mark.asyncio
async def test_prepare_rejects_changed_snapshot_for_same_order(monkeypatch):
    previous = {
        "id": 91, "order_id": 12, "currency": "USD",
        "policy_version": "kh-v1", "idempotency_key": "settlement-order-12-v1",
        "components": {"driver_earning": 800, "platform_fee": 200},
        "status": "PREPARED",
    }
    monkeypatch.setattr(settlement.db, "pool", Pool(FakeConnection(previous=previous)))

    with pytest.raises(settlement.SettlementConflict):
        await settlement.OrderSettlement.prepare(
            order_id=12, currency="USD", policy_version="kh-v1",
            idempotency_key="settlement-order-12-v1",
            components={"driver_earning": 700, "platform_fee": 300},
        )


@pytest.mark.asyncio
async def test_prepare_rejects_non_completed_order(monkeypatch):
    conn = FakeConnection(order_status="IN_PROGRESS")
    monkeypatch.setattr(settlement.db, "pool", Pool(conn))

    with pytest.raises(settlement.OrderNotSettleable):
        await settlement.OrderSettlement.prepare(
            order_id=12, currency="USD", policy_version="kh-v1",
            idempotency_key="settlement-order-12-v1", components={"fare": 1000},
        )
    assert not any("INSERT INTO order_settlements" in call[1] for call in conn.calls if call[0] == "fetchrow")


@pytest.mark.parametrize("components", [
    {}, {"Fare": 100}, {"fare": -1}, {"fare": True}, {"fare": 2**63},
])
def test_component_validation_rejects_invalid_amounts(components):
    with pytest.raises(ValueError):
        settlement._validate_components(components)


@pytest.mark.parametrize("kwargs", [
    {"order_id": True, "currency": "USD", "policy_version": "v1", "idempotency_key": "key", "components": {"fare": 1}},
    {"order_id": 1, "currency": "EUR", "policy_version": "v1", "idempotency_key": "key", "components": {"fare": 1}},
    {"order_id": 1, "currency": "USD", "policy_version": "", "idempotency_key": "key", "components": {"fare": 1}},
])
def test_prepare_rejects_invalid_contract_arguments(kwargs):
    with pytest.raises(ValueError):
        # Validation occurs before the database pool is accessed.
        import asyncio
        asyncio.run(settlement.OrderSettlement.prepare(**kwargs))
